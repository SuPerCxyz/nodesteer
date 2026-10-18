"""Frame proxy for the sys-b03 owned child Agent (runs on the operator workstation).

Sits between the owned child Agent (on repair native-3) and the repair Hub so
the harness can, without touching product code or any protected Agent:

  * cut / restore the child's Hub link (mode "pass" | "cut"), and
  * drop or duplicate the next N CHANGE_NOTIFICATION (Hub -> Agent) frames,
    which is exactly the real-time notification channel. The Agent's own
    periodic REVISION_CHECK / REVISION_CHECK_ACK / SYNC_* channel is left
    fully intact, so convergence must come from Revision Check.

Only frame type, direction and a few small metadata fields (global revision,
since/applied revision, object type/id, revision) are recorded. Payload
bodies, log content and credentials are never captured or persisted.
"""
import asyncio
import json
import threading
import time

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
        self._drop_notif_ids = set()
        self._drop_notif_done = []
        self._dup_notif_ids = set()
        self._dup_notif_done = []
        self._suppress = False
        self._suppressed = []
        self._connections = set()
        self.events = []
        self.notifications = []       # every CHANGE_NOTIFICATION with action taken
        self.revision_checks = []     # REVISION_CHECK / _ACK timeline
        self.sync_frames = []         # SYNC_REQUEST / _RESPONSE / _ACK timeline
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

    def arm_drop_notification(self, object_ids):
        """Drop the next CHANGE_NOTIFICATION (Hub->Agent) for each given object id.

        Keyed by object id so that concurrent batches' notifications are never
        affected: only the harness's own objects are ever dropped.
        """
        ids = [object_ids] if isinstance(object_ids, str) else list(object_ids)
        with self._lock:
            self._drop_notif_ids.update(ids)

    def arm_dup_notification(self, object_ids):
        """Duplicate the next CHANGE_NOTIFICATION for each given object id."""
        ids = [object_ids] if isinstance(object_ids, str) else list(object_ids)
        with self._lock:
            self._dup_notif_ids.update(ids)

    def suppress_notifications(self, on: bool):
        """Drop ALL CHANGE_NOTIFICATION frames to the owned child while on.

        Used to isolate the owned child from other concurrent batches'
        notifications so that convergence can only come from Revision Check.
        Only this harness's own child link is affected.
        """
        with self._lock:
            self._suppress = bool(on)

    def state(self) -> dict:
        with self._lock:
            return {
                'mode': self._mode,
                'suppress_notifications': self._suppress,
                'suppressed_notification_count': len(self._suppressed),
                'drop_notification_armed': sorted(self._drop_notif_ids),
                'drop_notification_done': list(self._drop_notif_done),
                'dup_notification_armed': sorted(self._dup_notif_ids),
                'dup_notification_done': list(self._dup_notif_done),
                'notification_frames': len(self.notifications),
                'revision_check_frames': len(self.revision_checks),
                'sync_frames': len(self.sync_frames),
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
    def _meta(message):
        """Extract only small, non-sensitive frame metadata."""
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
        if t == 'CHANGE_NOTIFICATION':
            m.update({'global_revision': p.get('global_revision'),
                      'object_type': p.get('object_type'), 'object_id': p.get('object_id'),
                      'object_revision': p.get('object_revision')})
        elif t in ('REVISION_CHECK', 'REVISION_CHECK_ACK', 'HEARTBEAT'):
            m.update({'global_rev': p.get('global_rev')})
        elif t == 'HELLO':
            m.update({'local_global_rev': p.get('local_global_rev')})
        elif t == 'HELLO_ACK':
            m.update({'desired_global_rev': p.get('desired_global_rev')})
        elif t == 'SYNC_REQUEST':
            m.update({'since': p.get('since')})
        elif t == 'SYNC_RESPONSE':
            m.update({'global_rev': p.get('global_rev'), 'full_resync': p.get('full_resync'),
                      'snapshot': p.get('snapshot'),
                      'scripts': len(p.get('scripts') or []), 'tasks': len(p.get('tasks') or []),
                      'schedules': len(p.get('schedules') or []),
                      'tombstones': len(p.get('tombstones') or [])})
        elif t == 'SYNC_ACK':
            m.update({'applied_rev': p.get('applied_rev'), 'ok': p.get('ok')})
        elif t == 'EXECUTION_FINISHED':
            m.update({'execution_id': p.get('execution_id')})
        return m

    async def _handler(self, client):
        with self._lock:
            cut = self._mode == 'cut'
        if cut:
            self._event('client_rejected_while_cut',
                        {'path': str(getattr(client, 'request', None) and client.request.path)})
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

    async def _relay(self, source, sink, direction):
        async for message in source:
            with self._lock:
                cut = self._mode == 'cut'
            if cut:
                return
            m = self._meta(message)
            ftype = (m or {}).get('type')
            at = now_utc()

            drop = False
            duplicate = False
            if direction == 'hub_to_agent' and ftype == 'CHANGE_NOTIFICATION':
                with self._lock:
                    oid = m.get('object_id')
                    if self._suppress:
                        self._suppressed.append({k: m.get(k) for k in
                                                 ('global_revision', 'object_type', 'object_id')})
                        if oid in self._drop_notif_ids:
                            self._drop_notif_ids.discard(oid)
                            self._drop_notif_done.append({k: m.get(k) for k in
                                                          ('global_revision', 'object_type',
                                                           'object_id', 'object_revision')})
                        drop = True
                    elif oid in self._drop_notif_ids:
                        self._drop_notif_ids.discard(oid)
                        self._drop_notif_done.append({k: m.get(k) for k in
                                                      ('global_revision', 'object_type', 'object_id', 'object_revision')})
                        drop = True
                    elif oid in self._dup_notif_ids:
                        self._dup_notif_ids.discard(oid)
                        self._dup_notif_done.append({'global_revision': m.get('global_revision'),
                                                      'object_id': oid})
                        duplicate = True

            if ftype in ('CHANGE_NOTIFICATION',):
                with self._lock:
                    self.notifications.append(dict(m, direction=direction, at_utc=at,
                                                   action='dropped' if drop else
                                                   ('duplicated' if duplicate else 'passed')))
                self._event('change_notification', {'action': 'dropped' if drop else
                                                    ('duplicated' if duplicate else 'passed'),
                                                    'global_revision': m.get('global_revision'),
                                                    'object_type': m.get('object_type'),
                                                    'object_id': m.get('object_id')})
            elif ftype in ('REVISION_CHECK', 'REVISION_CHECK_ACK'):
                with self._lock:
                    self.revision_checks.append(dict(m, direction=direction, at_utc=at))
                self._event('revision_check_frame', {'type': ftype, 'direction': direction,
                                                     'global_rev': m.get('global_rev')})
            elif ftype in ('SYNC_REQUEST', 'SYNC_RESPONSE', 'SYNC_ACK'):
                with self._lock:
                    self.sync_frames.append(dict(m, direction=direction, at_utc=at))
                self._event('sync_frame', dict(m, direction=direction))

            if not drop:
                await sink.send(message)
            if duplicate:
                await sink.send(message)
