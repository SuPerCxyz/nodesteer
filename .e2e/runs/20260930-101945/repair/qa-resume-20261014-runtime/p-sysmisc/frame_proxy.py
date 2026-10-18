"""Frame-level proxy for the p-sched owned child Agent(s) (runs on the workstation).

Sits between an owned child Agent (on native-2, through an ephemeral port) and
the repair Hub. It is a transparent pass-through that records an ordered,
timestamped frame ledger so the harness can prove, without touching product
code or any protected Agent/Hub:

  * which direction carried RUN_EXECUTION for a given task (hub-owned dispatch
    vs agent-owned local trigger),
  * which executions the Hub explicitly dispatched and which the Agent created
    locally and reported,
  * CHANGE_NOTIFICATION / SYNC_RESPONSE bookkeeping.

Only frame metadata is recorded - never credentials and never script bodies.
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
    def __init__(self, upstream_url: str, label: str = 'a', listen_host: str = '0.0.0.0'):
        self.upstream_url = upstream_url
        self.label = label
        self.listen_host = listen_host
        self.port = None
        self._loop = None
        self._server = None
        self._thread = None
        self._lock = threading.RLock()
        self._connections = set()
        self._agent_conn = None
        self.events = []
        self.frames = []
        self._seq = 0

    def state(self):
        with self._lock:
            return {
                'label': self.label,
                'upstream': self.upstream_url,
                'listen_port': self.port,
                'connection_open_count': len(self._connections),
                'frames_total': len(self.frames),
                'counts_by_type': self._counts(),
            }

    def _counts(self):
        c = {}
        for f in self.frames:
            c[f['type']] = c.get(f['type'], 0) + 1
        return c

    def frames_of(self, ftype):
        with self._lock:
            return [dict(f) for f in self.frames if f['type'] == ftype]

    # ---------- lifecycle ----------
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name='frame-proxy-' + self.label)
        self._thread.start()
        deadline = time.time() + 15
        while self.port is None and time.time() < deadline:
            time.sleep(0.02)
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
        for conn in list(self._connections):
            try:
                asyncio.run_coroutine_threadsafe(conn.close(), self._loop)
            except Exception:
                pass
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

    def _event(self, name, fields=None):
        rec = {'event': name, 'at_utc': now_utc()}
        rec.update(fields or {})
        with self._lock:
            self.events.append(rec)

    # ---------- frame bookkeeping ----------
    @staticmethod
    def _parse(message):
        if isinstance(message, bytes):
            return None
        try:
            env = json.loads(message)
        except Exception:
            return None
        return env if isinstance(env, dict) else None

    def _record(self, direction, env):
        ts = time.time()
        ftype = env.get('type')
        p = env.get('payload') if isinstance(env.get('payload'), dict) else {}
        with self._lock:
            self._seq += 1
            rec = {'seq': self._seq, 'at': ts, 'at_utc': now_utc(ts),
                   'direction': direction, 'type': ftype, 'env_id': env.get('id')}
            if ftype == 'RUN_EXECUTION':
                rec.update({k: p.get(k) for k in
                            ('execution_id', 'task_id', 'trigger_type', 'scheduled_time', 'retry', 'timeout')})
                rec['payload_type'] = p.get('type')
            elif ftype == 'CANCEL_EXECUTION':
                rec.update({k: p.get(k) for k in ('execution_id', 'reason')})
            elif ftype in ('EXECUTION_STARTED', 'EXECUTION_FINISHED'):
                rec.update({k: p.get(k) for k in
                            ('execution_id', 'task_id', 'status', 'exit_code', 'trigger_type', 'scheduled_time')})
            elif ftype == 'CHANGE_NOTIFICATION':
                rec.update({k: p.get(k) for k in ('object_type', 'object_id', 'object_revision', 'global_revision')})
            elif ftype in ('SYNC_RESPONSE',):
                rec['global_rev'] = p.get('global_rev')
                rec['counts'] = {k: len(p.get(k) or []) for k in ('scripts', 'tasks', 'schedules', 'apps', 'tombstones')}
            elif ftype == 'SYNC_REQUEST':
                rec['since'] = p.get('since')
            elif ftype in ('REVISION_CHECK', 'REVISION_CHECK_ACK', 'HEARTBEAT', 'HEARTBEAT_ACK', 'HELLO'):
                rec['global_rev'] = p.get('global_rev', p.get('local_global_rev'))
            elif ftype == 'HELLO_ACK':
                rec['accepted'] = p.get('accepted')
                rec['message'] = p.get('message')
            elif ftype == 'SYNC_ACK':
                rec.update({k: p.get(k) for k in ('ok', 'applied_rev', 'error')})
            self.frames.append(rec)
        return rec

    async def _handler(self, client):
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
            self._agent_conn = client
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
                if self._agent_conn is client:
                    self._agent_conn = None
            self._event('connection_closed', {'port': self.port})
            for conn in (client, hub):
                try:
                    await conn.close()
                except Exception:
                    pass

    async def _relay(self, source, sink, direction):
        async for message in source:
            env = self._parse(message)
            if env is None:
                await sink.send(message)
                continue
            self._record(direction, env)
            await sink.send(message)
