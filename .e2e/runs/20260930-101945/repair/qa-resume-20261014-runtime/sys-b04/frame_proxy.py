"""Frame proxy for the sys-b04 owned child Agent (runs on the operator workstation).

Sits between the owned child Agent (repair native-3) and the repair Hub so the
harness can, without touching product code, any protected Agent or the Hub:

  * cut / restore the child's Hub link (mode "pass" | "cut"),
  * duplicate the next CHANGE_NOTIFICATION(s) for chosen object ids,
  * defer (hold) the next CHANGE_NOTIFICATION(s) for chosen object ids and
    release them later - i.e. deliver an *older* notification *after* a newer
    one on purpose (real out-of-order delivery),
  * inject a synthetic Hub -> Agent CHANGE_NOTIFICATION carrying an explicitly
    chosen (older) revision, to replay a stale notification,
  * record an ordered, timestamped ledger of every control-plane frame.

Only frame type, direction and small metadata (object ids, revisions, snapshot
flags, execution ids) are recorded - never credentials, never script bodies and
never log content.
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
        self._dup = {}
        self._dup_done = []
        self._defer = {}
        self._deferred = []
        self._released = []
        self._agent_conn = None
        self._conns = set()
        self.frames = []
        self.notifications = []
        self.revision_checks = []
        self.sync_frames = []
        self.events = []
        self._seq = 0
        self._cut_events = 0
        self._upstream_dial_failures = 0

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

    def arm_dup_notification(self, object_ids, count=1):
        """Duplicate the next `count` Hub->Agent CHANGE_NOTIFICATION(s) per object id."""
        ids = [object_ids] if isinstance(object_ids, str) else list(object_ids)
        with self._lock:
            for oid in ids:
                self._dup[oid] = self._dup.get(oid, 0) + int(count)
        self._event('dup_notification_armed', {'object_ids': ids, 'count': count})

    def arm_defer_notification(self, object_ids, count=1):
        """Hold the next `count` Hub->Agent CHANGE_NOTIFICATION(s) per object id."""
        ids = [object_ids] if isinstance(object_ids, str) else list(object_ids)
        with self._lock:
            for oid in ids:
                self._defer[oid] = self._defer.get(oid, 0) + int(count)
        self._event('defer_notification_armed', {'object_ids': ids, 'count': count})

    def deferred_frames(self):
        with self._lock:
            return [{'seq': h['seq'], 'at_utc': h['at_utc'], 'object_id': h['meta'].get('object_id'),
                     'object_type': h['meta'].get('object_type'),
                     'global_revision': h['meta'].get('global_revision')} for h in self._deferred]

    def release_deferred(self):
        with self._lock:
            held = list(self._deferred)
            self._deferred = []
        for h in held:
            fut = asyncio.run_coroutine_threadsafe(self._send_agent(h['raw']), self._loop)
            try:
                fut.result(timeout=10)
            except Exception as exc:
                self._event('defer_release_failed', {'error': type(exc).__name__})
            with self._lock:
                self._released.append({'seq': h['seq'], 'at_utc': now_utc(),
                                       'object_id': h['meta'].get('object_id'),
                                       'global_revision': h['meta'].get('global_revision')})
        self._event('deferred_released', {'count': len(held)})
        return len(held)

    def inject_stale_notification(self, object_type, object_id, object_revision, global_revision):
        """Send a synthetic Hub->Agent CHANGE_NOTIFICATION carrying an older revision."""
        env = {'type': 'CHANGE_NOTIFICATION', 'id': '',
               'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
               'payload': {'object_type': object_type, 'object_id': object_id,
                           'object_revision': object_revision, 'global_revision': global_revision}}
        rec = {'object_type': object_type, 'object_id': object_id,
               'object_revision': object_revision, 'global_revision': global_revision,
               'role': 'harness_control_injection', 'at_utc': now_utc()}
        self._event('stale_change_notification_injected', rec)
        with self._lock:
            self.notifications.append(dict(rec, action='injected_stale', direction='hub_to_agent'))
            self._seq += 1
            self.frames.append(dict(rec, type='CHANGE_NOTIFICATION', direction='hub_to_agent',
                                    seq=self._seq, at=time.time()))
        try:
            fut = asyncio.run_coroutine_threadsafe(self._send_agent(json.dumps(env)), self._loop)
            fut.result(timeout=10)
        except Exception as exc:
            self._event('stale_injection_failed', {'error': type(exc).__name__})
            return False
        return True

    async def _send_agent(self, raw):
        conn = self._agent_conn
        if conn is None:
            raise RuntimeError('no_agent_connection')
        await conn.send(raw)

    def state(self) -> dict:
        with self._lock:
            return {
                'mode': self._mode,
                'pending_dup': dict(self._dup),
                'dup_done': list(self._dup_done),
                'pending_defer': dict(self._defer),
                'deferred_held': len(self._deferred),
                'deferred_released': list(self._released),
                'cut_events': self._cut_events,
                'upstream_dial_failures': self._upstream_dial_failures,
                'open_connections': len(self._conns),
                'frames_total': len(self.frames),
                'notification_frames': len(self.notifications),
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
            m.update({'local_global_rev': p.get('local_global_rev')})
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
            def _ids(key, limit=12):
                out = []
                for e in (p.get(key) or []):
                    if isinstance(e, dict) and e.get('id'):
                        out.append(e['id'])
                    if len(out) >= limit:
                        break
                return out
            m.update({'global_rev': p.get('global_rev'), 'full_resync': p.get('full_resync'),
                      'snapshot': p.get('snapshot'),
                      'scripts': len(p.get('scripts') or []), 'tasks': len(p.get('tasks') or []),
                      'schedules': len(p.get('schedules') or []),
                      'script_ids': _ids('scripts'), 'task_ids': _ids('tasks'),
                      'schedule_ids': _ids('schedules'),
                      'tombstones': [ta.get('object_id') for ta in (p.get('tombstones') or [])]})
        elif t == 'SYNC_ACK':
            m.update({'applied_rev': p.get('applied_rev'), 'ok': p.get('ok'), 'error': p.get('error')})
        elif t == 'RUN_EXECUTION':
            m.update({'execution_id': p.get('execution_id')})
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

    async def _handler(self, client):
        with self._lock:
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
            self._agent_conn = client
        self._event('connection_open', {'port': self.port})
        try:
            await asyncio.gather(self._relay(client, hub, 'agent_to_hub'),
                                 self._relay(hub, client, 'hub_to_agent'))
        except Exception as exc:
            self._event('relay_error', {'error': type(exc).__name__})
        finally:
            with self._lock:
                self._conns.discard(client)
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
            with self._lock:
                if self._mode == 'cut':
                    return
            m = self._meta(message)
            rec = self._record(direction, m)
            ftype = (m or {}).get('type')
            if m:
                self._event('frame', {'type': ftype, 'direction': direction,
                                      'global_rev': m.get('global_rev') or m.get('global_revision'),
                                      'since': m.get('since'), 'applied_rev': m.get('applied_rev'),
                                      'execution_id': m.get('execution_id')})
            if ftype in ('REVISION_CHECK', 'REVISION_CHECK_ACK'):
                with self._lock:
                    self.revision_checks.append({'type': ftype, 'direction': direction,
                                                 'global_rev': m.get('global_rev'),
                                                 'at_utc': rec['at_utc']})
            elif ftype in ('SYNC_REQUEST', 'SYNC_RESPONSE', 'SYNC_ACK'):
                with self._lock:
                    self.sync_frames.append(dict(m, direction=direction, at_utc=rec['at_utc']))

            drop = False
            duplicate = False
            defer = False
            if direction == 'hub_to_agent' and ftype == 'CHANGE_NOTIFICATION':
                oid = m.get('object_id')
                with self._lock:
                    if oid in self._dup and self._dup[oid] > 0:
                        self._dup[oid] -= 1
                        self._dup_done.append({'object_id': oid, 'global_revision': m.get('global_revision'),
                                               'at_utc': rec['at_utc']})
                        duplicate = True
                    elif oid in self._defer and self._defer[oid] > 0:
                        self._defer[oid] -= 1
                        defer = True
                        self._deferred.append({'seq': rec['seq'], 'at_utc': rec['at_utc'],
                                               'meta': m, 'raw': message})
                action = ('duplicated' if duplicate else 'deferred' if defer else 'passed')
                rec['action'] = action
                if duplicate:
                    rec['duplicated'] = True
                with self._lock:
                    self.notifications.append(dict(m, direction=direction, at_utc=rec['at_utc'],
                                                   action=action))
                self._event('change_notification', {'action': action, 'object_id': oid,
                                                    'global_revision': m.get('global_revision'),
                                                    'at_utc': rec['at_utc']})
            if drop:
                continue
            if not defer:
                await sink.send(message)
            if duplicate:
                await sink.send(message)
