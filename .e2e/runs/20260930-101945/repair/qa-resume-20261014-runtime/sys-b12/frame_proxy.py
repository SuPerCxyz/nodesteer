"""Recording websocket frame relay for the sys-b12 owned child-B (non-native) Agent.

It sits between the owned child Agent and the repair Hub so the harness can prove, without
touching product code or any protected Agent, that the Hub never dispatches a Host Operation
frame (DEPLOY_REQUEST / RUN_EXECUTION / ARTIFACT_PREFETCH / FILE_*_REQUEST) to a node whose
Capability set does not satisfy the required capability.

Only frame types, direction and a couple of non-secret correlation ids are recorded.
"""
import asyncio
import json
import threading
import time
from collections import Counter

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

MAX_MESSAGE = 64 << 20
HOST_OP_FRAMES = ('DEPLOY_REQUEST', 'RUN_EXECUTION', 'ARTIFACT_PREFETCH',
                  'FILE_UPLOAD_REQUEST', 'FILE_DELIVERY_REQUEST', 'REMOTE_STATE_REQ')


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


class RecordingProxy:
    def __init__(self, upstream_url: str, listen_host: str = '0.0.0.0'):
        self.upstream_url = upstream_url
        self.listen_host = listen_host
        self.port = None
        self._loop = None
        self._server = None
        self._thread = None
        self._lock = threading.Lock()
        self._agent_conn = None
        self._connections = set()
        self.events = []
        self.frame_counts = {'hub_to_agent': Counter(), 'agent_to_hub': Counter()}
        self.host_op_frames = []

    # ---------- lifecycle ----------
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name='rec-proxy')
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
        with self._lock:
            conns = list(self._connections)
        for conn in conns:
            try:
                asyncio.run_coroutine_threadsafe(conn.close(code=1001, reason='proxy stop'), self._loop)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=10)

    def summary(self):
        with self._lock:
            return {
                'port': self.port,
                'upstream': self.upstream_url,
                'frame_counts': {k: dict(v) for k, v in self.frame_counts.items()},
                'host_op_frames_hub_to_agent': list(self.host_op_frames),
                'connection_events': [e for e in self.events if not e['event'].startswith('frame')],
            }

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

    @staticmethod
    def _classify(message):
        if isinstance(message, bytes):
            return 'binary', {}
        try:
            env = json.loads(message)
        except Exception:
            return None, {}
        if not isinstance(env, dict):
            return None, {}
        payload = env.get('payload') or {}
        return env.get('type'), payload if isinstance(payload, dict) else {}

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
            ftype, payload = self._classify(message)
            if ftype:
                with self._lock:
                    self.frame_counts[direction][ftype] += 1
                if direction == 'hub_to_agent' and ftype in HOST_OP_FRAMES:
                    rec = {'type': ftype, 'at_utc': now_utc(),
                           'envelope_id': None}
                    if ftype in ('DEPLOY_REQUEST', 'ARTIFACT_PREFETCH'):
                        rec['app_id'] = payload.get('app_id') or payload.get('artifact_id')
                        rec['operation'] = payload.get('operation')
                    with self._lock:
                        self.host_op_frames.append(rec)
                    self._event('host_op_frame_hub_to_agent', rec)
            await sink.send(message)
