"""Frame proxy with fault injection for the qa-resume-20261016-g23 batch.

Adapted from qa-resume-20261014-runtime/sys-b05/frame_proxy.py (cut/restore,
duplicate, drop) and extended with a bounded *hold* (delayed delivery) and a
*replay* of a previously observed hub->agent frame (reordered / stale delivery).

It sits between the owned child Agent (on native-2, through an ephemeral port)
and the repair Hub.  Nothing here touches product code, protected Agents or the
repair Hub; every injected fault only affects the harness-owned child's own
websocket channel.

The ledger records frame metadata only (type, direction, envelope id, the
execution/task ids it needs for assertions, and - for the notification fault
injection - the raw frame text of CHANGE_NOTIFICATION frames so a genuine stale
frame can be replayed).  No credentials, no script bodies, no log bodies.
"""
import asyncio
import json
import threading
import time

from websockets.asyncio.client import connect as ws_connect
from websockets.asyncio.server import serve as ws_serve

MAX_MESSAGE = 64 << 20
RAW_KEEP_TYPES = {'CHANGE_NOTIFICATION', 'REVISION_CHECK', 'HEARTBEAT'}


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
        self._mode = 'pass'
        self._connections = set()
        self._upstreams = set()
        self._agent_conn = None
        self._port_locked = None
        self.events = []
        self.frames = []
        self._raw = {}
        self._seq = 0
        self._hold = []       # list of dicts: {ftype, direction, seconds, task_id, exec_id, armed_at}
        self._hold_done = []
        self._drop = []
        self._drop_done = []
        self._dup = []
        self._dup_done = []

    # ---------------- control ----------------
    def set_mode(self, mode: str):
        """pass = transparent relay; cut = hard partition.

        cut closes every relayed socket in both directions and then stops the
        listener entirely, so the agent's dials fail at the TCP level
        (ECONNREFUSED) and its reconnect state machine never sees a completed
        handshake.  pass re-binds the very same ephemeral port so the child's
        configuration stays valid."""
        if mode not in ('pass', 'cut'):
            raise ValueError('mode')
        with self._lock:
            prev = self._mode
            self._mode = mode
        if mode == 'cut':
            self._close_all()
            self._stop_listener()
        elif prev == 'cut':
            self._start_listener()
        self._event('mode_set', {'mode': mode, 'port': self.port})

    def mode(self):
        with self._lock:
            return self._mode

    def arm_hold(self, ftype, direction, seconds, task_id=None, exec_id=None, object_id=None, label=None):
        with self._lock:
            self._hold.append({'ftype': ftype, 'direction': direction, 'seconds': seconds,
                               'task_id': task_id, 'exec_id': exec_id, 'object_id': object_id, 'label': label})
        self._event('hold_armed', {'ftype': ftype, 'direction': direction, 'seconds': seconds,
                                   'task_id': task_id, 'exec_id': exec_id, 'object_id': object_id, 'label': label})

    def arm_drop(self, ftype, direction, count=1, task_id=None, exec_id=None, object_id=None, label=None):
        with self._lock:
            self._drop.append({'ftype': ftype, 'direction': direction, 'remaining': count,
                               'task_id': task_id, 'exec_id': exec_id, 'object_id': object_id, 'label': label})
        self._event('drop_armed', {'ftype': ftype, 'direction': direction, 'count': count,
                                   'task_id': task_id, 'exec_id': exec_id, 'object_id': object_id, 'label': label})

    def arm_duplicate(self, ftype, direction, count=1, task_id=None, exec_id=None, object_id=None, label=None):
        with self._lock:
            self._dup.append({'ftype': ftype, 'direction': direction, 'remaining': count,
                              'task_id': task_id, 'exec_id': exec_id, 'object_id': object_id, 'label': label})
        self._event('duplicate_armed', {'ftype': ftype, 'direction': direction, 'count': count,
                                        'task_id': task_id, 'exec_id': exec_id, 'object_id': object_id, 'label': label})

    def replay(self, seq, direction='hub_to_agent'):
        """Re-send a previously observed raw frame toward the agent (stale/reordered delivery)."""
        with self._lock:
            raw = self._raw.get(seq)
        if raw is None:
            raise RuntimeError('no_raw_frame_for_seq_%s' % seq)
        with self._lock:
            conn = self._agent_conn
        if conn is None:
            raise RuntimeError('no_agent_connection_for_replay')
        ev = {'event': 'replay_requested', 'at_utc': now_utc(), 'seq': seq,
              'raw_type': (json.loads(raw) or {}).get('type') if isinstance(raw, str) else None,
              'direction': direction}
        with self._lock:
            self.events.append(ev)
        asyncio.run_coroutine_threadsafe(conn.send(raw), self._loop).result(timeout=10)
        # record the injected frame in the ledger with its own synthetic seq
        env = json.loads(raw)
        rec = self._record(direction, env, injected=True, replayed_seq=seq)
        self._event('replay_sent', {'seq': seq, 'new_seq': rec['seq'], 'type': env.get('type')})
        return rec

    def state(self):
        with self._lock:
            return {
                'label': self.label, 'upstream': self.upstream_url, 'listen_port': self.port,
                'mode': self._mode, 'connection_open_count': len(self._connections),
                'frames_total': len(self.frames), 'counts_by_type': self._counts(),
                'hold_armed': list(self._hold), 'hold_done': list(self._hold_done),
                'drop_armed': list(self._drop), 'drop_done': list(self._drop_done),
                'duplicate_armed': list(self._dup), 'duplicate_done': list(self._dup_done),
            }

    def _counts(self):
        c = {}
        for f in self.frames:
            c[f['type']] = c.get(f['type'], 0) + 1
        return c

    def frames_of(self, ftype, direction=None, task_id=None):
        with self._lock:
            rows = [dict(f) for f in self.frames
                    if f['type'] == ftype
                    and (direction is None or f['direction'] == direction)
                    and (task_id is None or f.get('task_id') == task_id)]
        return rows

    def sync_requests_since(self, ts):
        with self._lock:
            return [dict(f) for f in self.frames if f['type'] == 'SYNC_REQUEST' and f['at'] >= ts]

    def raw_frames_of(self, ftype, direction=None):
        with self._lock:
            out = []
            for f in self.frames:
                if f['type'] != ftype:
                    continue
                if direction and f['direction'] != direction:
                    continue
                raw = self._raw.get(f['seq'])
                out.append({'seq': f['seq'], 'at_utc': f['at_utc'], 'direction': f['direction'],
                            'global_revision': f.get('global_revision'), 'raw': raw})
            return out

    # ---------------- lifecycle ----------------
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

    async def _serve(self, port=0):
        self._server = await ws_serve(self._handler, self.listen_host, port,
                                      subprotocols=['nodesteer'], compression=None,
                                      max_size=MAX_MESSAGE, ping_interval=None)
        self.port = self._server.sockets[0].getsockname()[1]
        if self._port_locked is None:
            self._port_locked = self.port
        self._event('proxy_listening', {'port': self.port, 'upstream': self.upstream_url})

    def _stop_listener(self):
        async def _shutdown():
            if self._server is not None:
                self._server.close()
                await self._server.wait_closed()
        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=8)
        except Exception:
            pass
        self._server = None
        self._event('listener_stopped', {'port': self.port})

    def _start_listener(self):
        last = None
        for _ in range(8):
            try:
                asyncio.run_coroutine_threadsafe(self._serve(self._port_locked), self._loop).result(timeout=8)
                self._event('listener_restarted', {'port': self.port})
                return True
            except Exception as exc:
                last = exc
                time.sleep(1.0)
        raise RuntimeError('listener_restart_failed:%s' % type(last).__name__)

    def _close_all(self):
        with self._lock:
            conns = [c for c in self._connections] + [c for c in self._upstreams]
            if self._agent_conn:
                conns.append(self._agent_conn)
        for conn in conns:
            try:
                asyncio.run_coroutine_threadsafe(conn.close(code=1001, reason='proxy cut'), self._loop)
            except Exception:
                pass
        self._event('connections_closed_for_cut', {'count': len(conns)})

    def _event(self, name, fields=None):
        rec = {'event': name, 'at_utc': now_utc()}
        rec.update(fields or {})
        with self._lock:
            self.events.append(rec)

    # ---------------- frame bookkeeping ----------------
    @staticmethod
    def _parse(message):
        if isinstance(message, bytes):
            return None
        try:
            env = json.loads(message)
        except Exception:
            return None
        return env if isinstance(env, dict) else None

    def _record(self, direction, env, injected=False, replayed_seq=None):
        ts = time.time()
        ftype = env.get('type')
        p = env.get('payload') if isinstance(env.get('payload'), dict) else {}
        with self._lock:
            self._seq += 1
            rec = {'seq': self._seq, 'at': ts, 'at_utc': now_utc(ts), 'direction': direction,
                   'type': ftype, 'env_id': env.get('id'), 'injected': injected,
                   'replayed_seq': replayed_seq}
            if ftype == 'RUN_EXECUTION':
                rec.update({k: p.get(k) for k in
                            ('execution_id', 'task_id', 'trigger_type', 'scheduled_time', 'retry', 'timeout')})
            elif ftype in ('EXECUTION_STARTED', 'EXECUTION_FINISHED'):
                rec.update({k: p.get(k) for k in
                            ('execution_id', 'task_id', 'status', 'exit_code', 'trigger_type', 'scheduled_time')})
            elif ftype == 'CHANGE_NOTIFICATION':
                rec.update({k: p.get(k) for k in
                            ('object_type', 'object_id', 'object_revision', 'global_revision')})
            elif ftype == 'SYNC_RESPONSE':
                rec['global_rev'] = p.get('global_rev')
                rec['counts'] = {k: len(p.get(k) or []) for k in ('scripts', 'tasks', 'schedules', 'apps', 'tombstones')}
            elif ftype == 'SYNC_REQUEST':
                rec['since'] = p.get('since')
            elif ftype in ('REVISION_CHECK', 'REVISION_CHECK_ACK', 'HEARTBEAT', 'HEARTBEAT_ACK', 'HELLO'):
                rec['global_rev'] = p.get('global_rev', p.get('local_global_rev'))
            elif ftype == 'HELLO_ACK':
                rec['accepted'] = p.get('accepted')
            elif ftype == 'EXECUTION_ACK':
                rec.update({k: p.get(k) for k in ('execution_id', 'ok', 'error')})
            self.frames.append(rec)
        return rec

    def _match(self, rule, direction, ftype, rec):
        if rule['direction'] != direction or rule['ftype'] != ftype:
            return False
        if rule.get('task_id') and rec.get('task_id') != rule['task_id']:
            return False
        if rule.get('exec_id') and rec.get('execution_id') != rule['exec_id']:
            return False
        if rule.get('object_id') and rec.get('object_id') != rule['object_id']:
            return False
        return True

    # ---------------- connection handling ----------------
    async def _handler(self, client):
        with self._lock:
            cut = self._mode == 'cut'
        if cut:
            self._event('client_rejected_while_cut', {'port': self.port})
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
            self._agent_conn = client
            self._upstreams.add(hub)
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
                self._upstreams.discard(hub)
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
                cut = self._mode == 'cut'
            if cut:
                return
            env = self._parse(message)
            if env is None:
                await sink.send(message)
                continue
            ftype = env.get('type')
            p = env.get('payload') if isinstance(env.get('payload'), dict) else {}
            rec = self._record(direction, env)

            # ---- injection decision (only for the harness-owned child channel) ----
            hold_sec = None
            duplicate = False
            drop = False
            with self._lock:
                for rule in self._hold:
                    if self._match(rule, direction, ftype, rec):
                        hold_sec = rule['seconds']
                        rule['matched'] = True
                        self._hold.remove(rule)
                        self._hold_done.append({'ftype': ftype, 'direction': direction,
                                                'seconds': rule['seconds'], 'seq': rec['seq'],
                                                'task_id': rec.get('task_id'),
                                                'execution_id': rec.get('execution_id'),
                                                'label': rule.get('label'), 'at_utc': rec['at_utc']})
                        break
                for rule in self._drop:
                    if rule['remaining'] > 0 and self._match(rule, direction, ftype, rec):
                        rule['remaining'] -= 1
                        drop = True
                        self._drop_done.append({'ftype': ftype, 'direction': direction, 'seq': rec['seq'],
                                                'task_id': rec.get('task_id'),
                                                'object_id': rec.get('object_id'),
                                                'global_revision': rec.get('global_revision'),
                                                'label': rule.get('label'), 'at_utc': rec['at_utc']})
                        break
                for rule in self._dup:
                    if rule['remaining'] > 0 and self._match(rule, direction, ftype, rec):
                        rule['remaining'] -= 1
                        duplicate = True
                        self._dup_done.append({'ftype': ftype, 'direction': direction, 'seq': rec['seq'],
                                               'task_id': rec.get('task_id'),
                                               'execution_id': rec.get('execution_id'),
                                               'object_id': rec.get('object_id'),
                                               'global_revision': rec.get('global_revision'),
                                               'label': rule.get('label'), 'at_utc': rec['at_utc']})
                        break
                if ftype in RAW_KEEP_TYPES:
                    try:
                        self._raw[rec['seq']] = message if isinstance(message, str) else None
                    except Exception:
                        pass

            if hold_sec:
                self._event('frame_held', {'seq': rec['seq'], 'type': ftype, 'direction': direction,
                                           'seconds': hold_sec})
                await asyncio.sleep(hold_sec)
                self._event('frame_released', {'seq': rec['seq'], 'type': ftype, 'direction': direction})
            if not drop:
                await sink.send(message)
            else:
                self._event('frame_dropped', {'seq': rec['seq'], 'type': ftype, 'direction': direction})
            if duplicate:
                self._event('frame_duplicated', {'seq': rec['seq'], 'type': ftype, 'direction': direction})
                await sink.send(message)
