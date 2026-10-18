"""Frame proxy for the batch09-settings owned child Agent (runs on the operator
workstation, 192.168.100.170).

It sits between the owned child Agent and the repair Hub so the harness can,
without touching product code or any protected Agent:

  * observe every Hub<->Agent frame type with a timestamp (heartbeat cadence,
    revision-check cadence),
  * capture the payload of every hub->agent SETTINGS frame (the settings
    broadcast) and the settings carried in HEARTBEAT_ACK,
  * duplicate the next EXECUTION_FINISHED frame for a chosen Execution ID
    (real duplicate upload -> idempotency),
  * drop the next EXECUTION_ACK frame for a chosen Execution ID (forces the
    child journal to stay unsynced -> real re-upload on reconnect),
  * cut / restore the child's Hub connection.

Only frame type + execution id + direction + the settings scalar fields are
recorded; no credentials, no log bodies.
"""
import asyncio
import json
import threading
import time

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

MAX_MESSAGE = 64 << 20
SETTING_KEYS = ('heartbeat_sec', 'revision_check_sec', 'max_log_bytes')


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class FrameProxy:
    def __init__(self, upstream_url: str, listen_host: str = '0.0.0.0'):
        self.upstream_url = upstream_url
        self.listen_host = listen_host
        self.port = None
        self._loop = None
        self._server = None
        self._thread = None
        self._lock = threading.RLock()   # reentrant: _event() is called from locked regions
        self._mode = 'pass'
        self._dup_ids = set()
        self._dup_done = set()
        self._drop_ids = set()
        self._drop_done = set()
        self._drop_next = 0
        self._drop_next_done = []
        self._connections = set()
        self.events = []
        self._seen_finished = []
        self._seen_ack = []
        self._cut_events = 0
        # --- settings observation ---
        self._frames = []            # [{'dir','type','t'}] capped
        self._settings_frames = []   # hub->agent SETTINGS payloads
        self._heartbeat_acks = []    # hub->agent HEARTBEAT_ACK payloads
        self._by_type = {}

    # ---------- control ----------
    def set_mode(self, mode: str):
        if mode not in ('pass', 'cut'):
            raise ValueError('mode')
        with self._lock:
            self._mode = mode
        if mode == 'cut':
            self._close_all()

    def mode(self) -> str:
        with self._lock:
            return self._mode

    def arm_dup(self, exec_id: str):
        with self._lock:
            self._dup_ids.add(exec_id)

    def arm_drop_ack(self, exec_id: str):
        with self._lock:
            self._drop_ids.add(exec_id)

    def arm_drop_next_ack(self, count: int = 1):
        with self._lock:
            self._drop_next = count

    def state(self) -> dict:
        with self._lock:
            return {
                'mode': self._mode,
                'dup_armed': sorted(self._dup_ids), 'dup_done': sorted(self._dup_done),
                'drop_ack_armed': sorted(self._drop_ids), 'drop_ack_done': sorted(self._drop_done),
                'drop_next_remaining': self._drop_next, 'drop_next_done': list(self._drop_next_done),
                'seen_exec_finished': list(self._seen_finished),
                'seen_ack': list(self._seen_ack),
                'frame_type_counts': dict(self._by_type),
                'settings_frames': list(self._settings_frames),
                'heartbeat_ack_count': len(self._heartbeat_acks),
                'cut_events': self._cut_events,
                'connection_open_count': len(self._connections),
                'frames_recorded': len(self._frames),
            }

    def frame_log(self, frame_types=None):
        """Ordered (t, dir, type) list, optionally filtered to given types."""
        with self._lock:
            rows = list(self._frames)
        if frame_types:
            rows = [r for r in rows if r['type'] in frame_types]
        return rows

    def clear_frames(self):
        with self._lock:
            self._frames = []
            self._by_type = {}

    def connection_span(self):
        """Earliest/last recorded frame timestamps (monotonic) for cadence math."""
        with self._lock:
            if not self._frames:
                return None
            return {'first': self._frames[0]['t'], 'last': self._frames[-1]['t'],
                    'count': len(self._frames)}

    # ---------- lifecycle ----------
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name='frame-proxy')
        self._thread.start()
        deadline = time.time() + 15
        while self.port is None and time.time() < deadline:
            time.sleep(0.05)
        if self.port is None:
            raise RuntimeError('proxy_did_not_start')
        return self.port

    def stop(self):
        if self._loop is None or self._loop.is_closed():
            return
        async def _shutdown():
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
        try:
            fut = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
            fut.result(timeout=8)
        except Exception:
            pass
        self._close_all()
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
            self._loop.run_forever()
        finally:
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    async def _serve(self):
        self._server = await ws_serve(self._handler, self.listen_host, 0,
                                      subprotocols=['nodesteer'], compression=None,
                                      max_size=MAX_MESSAGE, ping_interval=None)
        self.port = self._server.sockets[0].getsockname()[1]
        self._event('proxy_listening', {'port': self.port, 'upstream': self.upstream_url})

    def _close_all(self):
        with self._lock:
            self._cut_events += 1
            conns = list(self._connections)
        for conn in conns:
            try:
                asyncio.run_coroutine_threadsafe(conn.close(code=1001, reason='proxy cut'), self._loop)
            except Exception:
                pass

    def _event(self, name, fields=None):
        rec = {'event': name, 'at_utc': now_utc()}
        rec.update(fields or {})
        with self._lock:
            self.events.append(rec)

    @staticmethod
    def _classify(message):
        if isinstance(message, bytes):
            return None, None, None
        try:
            env = json.loads(message)
        except Exception:
            return None, None, None
        if not isinstance(env, dict):
            return None, None, None
        payload = env.get('payload') or {}
        exec_id = payload.get('execution_id') if isinstance(payload, dict) else None
        return env.get('type'), exec_id, (payload if isinstance(payload, dict) else {})

    async def _handler(self, client):
        with self._lock:
            cut = self._mode == 'cut'
        if cut:
            self._event('client_rejected_while_cut', {'path': str(getattr(client, 'request', None) and client.request.path)})
            await client.close(code=1013, reason='hub cut by harness')
            return
        try:
            hub = await ws_connect(self.upstream_url, subprotocols=['nodesteer'],
                                   compression=None, max_size=MAX_MESSAGE, proxy=None,
                                   ping_interval=None, open_timeout=10)
        except Exception as exc:
            self._event('upstream_dial_failed', {'error': type(exc).__name__})
            await client.close(code=1011, reason='upstream dial failed')
            return
        with self._lock:
            self._connections.add(client)
        self._event('connection_open', {'port': self.port})
        try:
            await asyncio.gather(
                self._relay(client, hub, 'agent_to_hub'),
                self._relay(hub, client, 'hub_to_agent'),
            )
        except Exception as exc:
            self._event('relay_error', {'error': type(exc).__name__})
        finally:
            with self._lock:
                self._connections.discard(client)
            self._event('connection_closed', {'port': self.port})
            for conn in (client, hub):
                try:
                    await conn.close()
                except Exception:
                    pass

    def _record(self, direction, ftype, payload):
        now = time.time()
        broadcast = None
        with self._lock:
            self._frames.append({'t': now, 'dir': direction, 'type': ftype})
            if len(self._frames) > 40000:
                del self._frames[:10000]
            key = direction + ':' + str(ftype)
            self._by_type[key] = self._by_type.get(key, 0) + 1
            if direction == 'hub_to_agent' and ftype == 'SETTINGS':
                broadcast = {k: payload.get(k) for k in SETTING_KEYS if k in payload}
                self._settings_frames.append({
                    'at_utc': now_utc(), 'monotonic': now, 'payload': broadcast})
            if direction == 'hub_to_agent' and ftype == 'HEARTBEAT_ACK':
                self._heartbeat_acks.append({
                    'at_utc': now_utc(), 'monotonic': now,
                    'payload': {k: payload.get(k) for k in SETTING_KEYS if k in payload}})
        # _event() takes the lock again -> always call it OUTSIDE the locked region
        if broadcast is not None:
            self._event('settings_broadcast_seen', {'payload': broadcast})

    async def _relay(self, source, sink, direction):
        async for message in source:
            with self._lock:
                cut = self._mode == 'cut'
            if cut:
                return
            ftype, exec_id, payload = self._classify(message)
            if ftype:
                self._record(direction, ftype, payload)
            if direction == 'agent_to_hub' and ftype == 'EXECUTION_FINISHED':
                with self._lock:
                    self._seen_finished.append({'execution_id': exec_id, 'at_utc': now_utc()})
                self._event('exec_finished_seen', {'execution_id': exec_id})
            if direction == 'hub_to_agent' and ftype == 'EXECUTION_ACK':
                with self._lock:
                    self._seen_ack.append({'execution_id': exec_id, 'at_utc': now_utc()})
                self._event('exec_ack_seen', {'execution_id': exec_id})

            duplicate = False
            if direction == 'agent_to_hub' and ftype == 'EXECUTION_FINISHED' and exec_id:
                with self._lock:
                    if exec_id in self._dup_ids and exec_id not in self._dup_done:
                        self._dup_done.add(exec_id)
                        duplicate = True
            drop = False
            if direction == 'hub_to_agent' and ftype == 'EXECUTION_ACK' and exec_id:
                with self._lock:
                    if exec_id in self._drop_ids and exec_id not in self._drop_done:
                        self._drop_done.add(exec_id)
                        drop = True
                    elif self._drop_next > 0:
                        self._drop_next -= 1
                        self._drop_next_done.append(exec_id)
                        drop = True

            if not drop:
                await sink.send(message)
            if duplicate:
                self._event('exec_finished_duplicated', {'execution_id': exec_id})
                await sink.send(message)
            elif drop:
                self._event('exec_ack_dropped', {'execution_id': exec_id})
