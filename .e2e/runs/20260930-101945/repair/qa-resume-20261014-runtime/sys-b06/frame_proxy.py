"""Frame proxy for the sys-b01 owned child Agent (runs on the operator workstation).

Sits between the owned child Agent (repair native-1) and the repair Hub so the
harness can, without touching product code, any protected Agent or the Hub:

  * cut / restore the child's Hub link (mode "pass" | "cut"),
  * hold every Hub -> Agent SYNC_RESPONSE in a buffer until explicitly released,
    which widens the real reconnect Reconciliation window so the "no READY
    before reconciliation" behaviour can be exercised through the normal
    product path (a manual Run dispatched during that window),
  * keep an ordered, timestamped frame ledger of the control-plane frames.

Only frame type, direction and small metadata (revisions, ids, snapshot flags)
are recorded - never credentials and never script bodies.
"""
import asyncio
import json
import threading
import time

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

MAX_MESSAGE = 64 << 20


def now_utc(ts=None):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(ts if ts is not None else time.time()))


class FrameProxy:
    def __init__(self, upstream_url: str, listen_host: str = '0.0.0.0'):
        self.upstream_url = upstream_url
        self.listen_host = listen_host
        self.port = None
        self._loop = None
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self._mode = 'pass'
        self._hold_sync = False
        self._held_sync = []
        self._held_released = []
        self._conns = set()
        self.frames = []
        self.events = []
        self._seq = 0
        self._cut_events = 0
        self._client_connects = 0
        self._upstream_dial_failures = 0
        self._drop_hub_types = set()
        self._dropped_hub_frames = []

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

    def arm_delay_sync_response(self, seconds: float):
        """Deprecated one-shot delay; kept as an explicit error to avoid misuse."""
        raise RuntimeError('use_hold_sync_responses_instead')

    def hold_sync_responses(self, on: bool = True):
        """Buffer every Hub -> Agent SYNC_RESPONSE until released."""
        with self._lock:
            self._hold_sync = bool(on)
        self._event('hold_sync_responses', {'on': bool(on)})

    def set_drop_hub_types(self, types):
        """Drop (never deliver) every Hub -> Agent frame whose type is in `types`.

        Used to make the Agent keep a Journal row unsynced: the product only marks
        an Execution synced after EXECUTION_ACK, so withholding that ACK forces a
        genuine product-path re-upload on the next reconnect.
        """
        with self._lock:
            self._drop_hub_types = {str(t) for t in (types or [])}
        self._event('drop_hub_types_set', {'types': sorted(self._drop_hub_types)})

    def held_count(self):
        with self._lock:
            return len(self._held_sync)

    def pending_frames(self):
        with self._lock:
            return [{'seq': h['seq'], 'at_utc': h['at_utc'],
                     'global_rev': h['meta'].get('global_rev'),
                     'since': h['meta'].get('since')} for h in self._held_sync]

    def release_sync_responses(self):
        """Flush buffered SYNC_RESPONSE frames to the Agent in arrival order."""
        with self._lock:
            held = list(self._held_sync)
            self._held_sync = []
            self._hold_sync = False
        for h in held:
            fut = asyncio.run_coroutine_threadsafe(h['sink'].send(h['message']), self._loop)
            try:
                fut.result(timeout=10)
            except Exception as exc:
                self._event('release_send_failed', {'error': type(exc).__name__})
            with self._lock:
                self._held_released.append({'seq': h['seq'], 'at_utc': now_utc()})
        self._event('sync_responses_released', {'count': len(held)})
        return len(held)

    def state(self) -> dict:
        with self._lock:
            return {
                'mode': self._mode,
                'hold_sync_responses': self._hold_sync,
                'held_sync_responses': len(self._held_sync),
                'released_sync_responses': len(self._held_released),
                'cut_events': self._cut_events,
                'client_connects': self._client_connects,
                'upstream_dial_failures': self._upstream_dial_failures,
                'open_connections': len(self._conns),
                'frames_total': len(self.frames),
                'drop_hub_types': sorted(self._drop_hub_types),
                'dropped_hub_frames': len(self._dropped_hub_frames),
            }

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
            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=8)
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
            conns = list(self._conns)
        for conn in conns:
            try:
                asyncio.run_coroutine_threadsafe(conn.close(code=1001, reason='proxy cut'), self._loop)
            except Exception:
                pass

    def _event(self, name, fields=None):
        rec = {'event': name, 'at_utc': now_utc(), 'at': time.time()}
        rec.update(fields or {})
        with self._lock:
            self.events.append(rec)

    # ---------- frame bookkeeping ----------
    @staticmethod
    def _meta(message):
        if isinstance(message, bytes):
            return None
        try:
            env = json.loads(message)
        except Exception:
            return None
        if not isinstance(env, dict):
            return None
        t = env.get('type')
        p = env.get('payload') if isinstance(env.get('payload'), dict) else {}
        m = {'type': t}
        if t == 'HELLO':
            m.update({'local_global_rev': p.get('local_global_rev'), 'agent_id': p.get('agent_id')})
        elif t == 'HELLO_ACK':
            m.update({'accepted': p.get('accepted'), 'desired_global_rev': p.get('desired_global_rev')})
        elif t == 'CHANGE_NOTIFICATION':
            m.update({'global_revision': p.get('global_revision'), 'object_type': p.get('object_type'),
                      'object_id': p.get('object_id'), 'object_revision': p.get('object_revision')})
        elif t in ('REVISION_CHECK', 'REVISION_CHECK_ACK', 'HEARTBEAT', 'HEARTBEAT_ACK'):
            m.update({'global_rev': p.get('global_rev')})
        elif t == 'SYNC_REQUEST':
            m.update({'since': p.get('since')})
        elif t == 'SYNC_RESPONSE':
            m.update({'global_rev': p.get('global_rev'), 'full_resync': p.get('full_resync'),
                      'snapshot': p.get('snapshot'),
                      'scripts': len(p.get('scripts') or []), 'tasks': len(p.get('tasks') or []),
                      'schedules': len(p.get('schedules') or []),
                      'tombstones': len(p.get('tombstones') or [])})
        elif t == 'SYNC_ACK':
            m.update({'applied_rev': p.get('applied_rev'), 'ok': p.get('ok'), 'error': p.get('error')})
        elif t == 'RUN_EXECUTION':
            m.update({'execution_id': p.get('execution_id'), 'task_id': p.get('task_id')})
        elif t == 'EXECUTION_FINISHED':
            m.update({'execution_id': p.get('execution_id'), 'status': p.get('status')})
        elif t == 'EXECUTION_ACK':
            m.update({'execution_id': p.get('execution_id')})
        return m

    def _record(self, direction, m):
        ts = time.time()
        with self._lock:
            self._seq += 1
            rec = dict(m or {})
            rec.update({'seq': self._seq, 'at': ts, 'at_utc': now_utc(ts), 'direction': direction})
            self.frames.append(rec)
        return rec

    # ---------- relay ----------
    async def _handler(self, client):
        with self._lock:
            self._client_connects += 1
            cut = self._mode == 'cut'
        if cut:
            await client.close(code=1013, reason='hub cut by harness')
            return
        try:
            hub = await ws_connect(self.upstream_url, subprotocols=['nodesteer'],
                                   compression=None, max_size=MAX_MESSAGE, proxy=None,
                                   ping_interval=None, open_timeout=8)
        except Exception as exc:
            with self._lock:
                self._upstream_dial_failures += 1
            self._event('upstream_dial_failed', {'error': type(exc).__name__})
            await client.close(code=1011, reason='upstream dial failed')
            return
        with self._lock:
            self._conns.add(client)
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
                self._conns.discard(client)
            self._event('connection_closed', {'port': self.port})
            for conn in (client, hub):
                try:
                    await conn.close()
                except Exception:
                    pass

    async def _relay(self, source, sink, direction):
        async for message in source:
            with self._lock:
                if self._mode == 'cut':
                    return
            m = self._meta(message)
            rec = self._record(direction, m)
            if m:
                self._event('frame', {'type': m.get('type'), 'direction': direction,
                                      'global_rev': m.get('global_rev') or m.get('global_revision'),
                                      'since': m.get('since'), 'applied_rev': m.get('applied_rev'),
                                      'execution_id': m.get('execution_id')})
            if direction == 'hub_to_agent' and (m or {}).get('type') == 'SYNC_RESPONSE':
                with self._lock:
                    hold = self._hold_sync
                    if hold:
                        self._held_sync.append({'seq': rec['seq'], 'at_utc': rec['at_utc'],
                                                'meta': m, 'message': message, 'sink': sink})
                if hold:
                    self._event('sync_response_held', {'seq': rec['seq'],
                                                       'global_rev': m.get('global_rev'),
                                                       'held_total': self.held_count()})
                    continue
            if direction == 'hub_to_agent' and m and m.get('type') in self._drop_hub_types:
                with self._lock:
                    self._dropped_hub_frames.append({'seq': rec['seq'], 'at_utc': rec['at_utc'],
                                                     'type': m.get('type')})
                self._event('hub_frame_dropped', {'seq': rec['seq'], 'type': m.get('type')})
                continue
            await sink.send(message)
