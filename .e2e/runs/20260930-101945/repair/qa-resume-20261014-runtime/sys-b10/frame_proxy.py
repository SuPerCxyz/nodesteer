"""Frame proxy for the sys-b10 owned child Agent (runs on the operator workstation).

It sits between the owned child Agent and the repair Hub so the harness can,
without touching product code or any protected Agent:

  * relay the real Hub <-> owned child websocket,
  * inject a synthetic Hub->Agent frame (ARTIFACT_PREFETCH) to drive the Agent's
    real artifact download path against a harness-controlled URL / expected SHA.

Only frame type + artifact id/sha-prefix + direction are recorded; no payloads,
no credentials, no log bodies.
"""
import asyncio
import hashlib
import json
import threading
import time
from typing import Optional

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

MAX_MESSAGE = 64 << 20


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


def _sha_tag(sha):
    if not sha or len(sha) < 12:
        return sha or None
    return sha[:12]


class FrameProxy:
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
        self._injected = []
        self._seen_prefetch = []

    # ---------- control ----------
    def state(self) -> dict:
        with self._lock:
            return {
                'connection_open_count': len(self._connections),
                'agent_connected': self._agent_conn is not None,
                'injected_frames': list(self._injected),
                'seen_prefetch_from_hub': list(self._seen_prefetch),
            }

    def inject_artifact_prefetch(self, url, sha256, artifact_id='', size=0, timeout=15):
        """Send a synthetic ARTIFACT_PREFETCH from Hub->Agent through the live socket."""
        envelope = {
            'type': 'ARTIFACT_PREFETCH',
            'id': 'harness-sysb10-' + hashlib.sha256((url + sha256).encode()).hexdigest()[:12],
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'payload': {'artifact_id': artifact_id, 'url': url, 'sha256': sha256, 'size': size},
        }
        return self._inject(envelope)

    def _inject(self, envelope):
        msg = json.dumps(envelope)
        fut = asyncio.run_coroutine_threadsafe(self._send_agent(msg), self._loop)
        fut.result(timeout=15)
        payload = envelope.get('payload') or {}
        rec = {'type': envelope.get('type'), 'sha_prefix': _sha_tag(payload.get('sha256')),
               'url_host': _host(payload.get('url')), 'at_utc': now_utc()}
        with self._lock:
            self._injected.append(rec)
        self._event('hub_frame_injected', rec)
        return rec

    async def _send_agent(self, msg):
        with self._lock:
            conn = self._agent_conn
        if conn is None:
            raise RuntimeError('no_agent_connection_for_injection')
        await conn.send(msg)

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
            conns = list(self._connections)
        for conn in conns:
            try:
                asyncio.run_coroutine_threadsafe(conn.close(code=1001, reason='proxy stop'), self._loop)
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
            return None, None
        try:
            env = json.loads(message)
        except Exception:
            return None, None
        if not isinstance(env, dict):
            return None, None
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
            if direction == 'hub_to_agent' and ftype == 'ARTIFACT_PREFETCH':
                rec = {'sha_prefix': _sha_tag(payload.get('sha256')),
                       'artifact_id': payload.get('artifact_id'),
                       'url_host': _host(payload.get('url')), 'at_utc': now_utc()}
                with self._lock:
                    self._seen_prefetch.append(rec)
                self._event('prefetch_seen_from_hub', rec)
            await sink.send(message)


def _host(url):
    if not url:
        return None
    try:
        from urllib.parse import urlsplit
        s = urlsplit(url)
        return '%s://%s' % (s.scheme, s.netloc)
    except Exception:
        return 'unparseable'
