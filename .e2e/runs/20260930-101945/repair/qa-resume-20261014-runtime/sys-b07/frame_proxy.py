"""Frame proxy for the sys-b05 owned child Agent (runs on the operator workstation).

It sits between the owned child Agent and the repair Hub so the harness can,
without touching product code or any protected Agent:

  * cut / restore the child's Hub connection (mode "pass" | "cut"),
  * duplicate the next EXECUTION_FINISHED frame for a chosen Execution ID
    (real duplicate upload),
  * drop the next EXECUTION_ACK frame for a chosen Execution ID (forces the
    child journal to stay unsynced -> real re-upload on reconnect).

Only frame type + execution id + direction are recorded; no payloads, no
credentials, no log bodies.
"""
import asyncio
import json
import threading
import time
from typing import Optional

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

MAX_MESSAGE = 64 << 20


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
        self._lock = threading.Lock()
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

    def events_snapshot(self, name=None):
        with self._lock:
            evs = list(self.events)
        if name is None:
            return evs
        return [e for e in evs if e.get('event') == name]

    def state(self) -> dict:
        with self._lock:
            return {
                'mode': self._mode,
                'dup_armed': sorted(self._dup_ids), 'dup_done': sorted(self._dup_done),
                'drop_ack_armed': sorted(self._drop_ids), 'drop_ack_done': sorted(self._drop_done),
                'drop_next_remaining': self._drop_next, 'drop_next_done': list(self._drop_next_done),
                'seen_exec_finished': list(self._seen_finished),
                'seen_ack': list(self._seen_ack),
                'cut_events': self._cut_events,
                'connection_open_count': len(self._connections),
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
            return None, None
        try:
            env = json.loads(message)
        except Exception:
            return None, None
        if not isinstance(env, dict):
            return None, None
        payload = env.get('payload') or {}
        exec_id = payload.get('execution_id') if isinstance(payload, dict) else None
        return env.get('type'), exec_id

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
            # FIRST_COMPLETED + cancel the sibling: when either side closes (agent
            # disconnect / cut), the other socket must be torn down too, otherwise
            # the Hub never observes the disconnect and keeps the node online.
            tasks = [asyncio.ensure_future(self._relay(client, hub, 'agent_to_hub')),
                     asyncio.ensure_future(self._relay(hub, client, 'hub_to_agent'))]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for t in tasks:
                if t.done() and not t.cancelled():
                    exc = t.exception()
                    if exc:
                        self._event('relay_error', {'error': type(exc).__name__})
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

    async def _relay(self, source, sink, direction):
        async for message in source:
            with self._lock:
                cut = self._mode == 'cut'
            if cut:
                return
            ftype, exec_id = self._classify(message)
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
