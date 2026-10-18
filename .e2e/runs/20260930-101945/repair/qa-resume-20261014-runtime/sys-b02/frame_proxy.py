"""Frame-level proxy for the sys-b02 owned child Agent (runs on the workstation).

It sits between the owned child Agent (native-1, through an ephemeral port) and
the repair Hub so the harness can, without touching product code, any protected
Agent or the Hub:

  * cut / restore the child's Hub connection (mode "pass" | "cut"),
  * drop or duplicate CHANGE_NOTIFICATION frames (lost / duplicate notification),
  * inject a synthetic Hub->Agent CHANGE_NOTIFICATION (detector control),
  * corrupt exactly one SYNC_RESPONSE in flight (bad script SHA, or a phantom
    script entry plus a malformed task entry to force an in-transaction failure),
  * record an ordered, timestamped frame ledger for every control frame.

Only frame metadata (type, direction, object/execution ids, revisions, compact
payload key sets) is recorded - never credentials and never script bodies.
"""
import asyncio
import json
import threading
import time
from typing import Optional

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
        self._lock = threading.RLock()
        self._mode = 'pass'
        self._notif_mode = 'pass'          # pass | drop | dup
        self._notif_dup_target = None      # object_id to duplicate (None = any)
        self._notif_dup_done = set()
        self._sync_mutation = None         # armed one-shot SYNC_RESPONSE mutation
        self._sync_mutation_done = False
        self._connections = set()
        self._agent_conn = None            # server-side connection to the Agent
        self.events = []
        self.frames = []
        self._seq = 0
        self._cut_events = 0
        self._injected = []
        self._sync_mutations_applied = []

    # ---------- control ----------
    def set_mode(self, mode: str):
        if mode not in ('pass', 'cut'):
            raise ValueError('mode')
        with self._lock:
            self._mode = mode
        if mode == 'cut':
            self._close_all()

    def mode(self):
        with self._lock:
            return self._mode

    def set_notif_mode(self, mode, object_id=None):
        if mode not in ('pass', 'drop', 'dup'):
            raise ValueError('notif mode')
        with self._lock:
            self._notif_mode = mode
            self._notif_dup_target = object_id
        self._event('notif_mode_set', {'mode': mode, 'object_id': object_id})

    def arm_sync_mutation(self, mutation):
        with self._lock:
            self._sync_mutation = mutation
            self._sync_mutation_done = False
        self._event('sync_mutation_armed', {'mode': (mutation or {}).get('mode')})

    # ---------- injection ----------
    def inject_change_notification(self, object_type, object_id, object_revision, global_revision):
        env = {
            'type': 'CHANGE_NOTIFICATION',
            'id': '',
            'timestamp': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'payload': {
                'object_type': object_type,
                'object_id': object_id,
                'object_revision': object_revision,
                'global_revision': global_revision,
            },
        }
        rec = {'object_type': object_type, 'object_id': object_id,
               'object_revision': object_revision, 'global_revision': global_revision,
               'role': 'harness_control_injection', 'at': time.time(), 'at_utc': now_utc()}
        with self._lock:
            self._injected.append(rec)
        self._event('change_notification_injected', rec)
        future = asyncio.run_coroutine_threadsafe(self._send_agent(json.dumps(env)), self._loop)
        try:
            future.result(timeout=10)
        except Exception as exc:
            self._event('inject_failed', {'error': type(exc).__name__})
            return False
        return True

    async def _send_agent(self, msg):
        conn = self._agent_conn
        if conn is None:
            raise RuntimeError('no_agent_connection_for_injection')
        await conn.send(msg)

    # ---------- state ----------
    def state(self):
        with self._lock:
            return {
                'mode': self._mode,
                'notif_mode': self._notif_mode,
                'notif_dup_target': self._notif_dup_target,
                'notif_dup_done': sorted(self._notif_dup_done),
                'sync_mutation': self._sync_mutation,
                'sync_mutation_done': self._sync_mutation_done,
                'cut_events': self._cut_events,
                'connection_open_count': len(self._connections),
                'frames_total': len(self.frames),
                'injected_frames': list(self._injected),
            }

    def frame_summary(self):
        with self._lock:
            return list(self.frames)

    # ---------- lifecycle ----------
    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name='frame-proxy')
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
                   'direction': direction, 'type': ftype}
            if ftype == 'CHANGE_NOTIFICATION':
                rec.update({k: p.get(k) for k in ('object_type', 'object_id', 'object_revision', 'global_revision')})
                rec['payload_keys'] = sorted(p.keys())
            elif ftype == 'SYNC_RESPONSE':
                rec['global_rev'] = p.get('global_rev')
                rec['snapshot'] = p.get('snapshot')
                rec['full_resync'] = p.get('full_resync')
                rec['counts'] = {k: len(p.get(k) or []) for k in ('scripts', 'tasks', 'schedules', 'apps', 'tombstones')}
            elif ftype == 'SYNC_REQUEST':
                rec['since'] = p.get('since')
            elif ftype in ('SYNC_ACK',):
                rec['ok'] = p.get('ok')
                rec['applied_rev'] = p.get('applied_rev')
                rec['error'] = p.get('error')
            elif ftype in ('REVISION_CHECK', 'REVISION_CHECK_ACK', 'HEARTBEAT', 'HEARTBEAT_ACK'):
                rec['global_rev'] = p.get('global_rev')
            elif ftype == 'HELLO':
                rec['local_global_rev'] = p.get('local_global_rev')
            elif ftype == 'HELLO_ACK':
                rec['accepted'] = p.get('accepted')
                rec['desired_global_rev'] = p.get('desired_global_rev')
            self.frames.append(rec)
        return rec

    # ---------- relay ----------
    async def _handler(self, client):
        with self._lock:
            if self._mode == 'cut':
                cut = True
            else:
                cut = False
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
            with self._lock:
                if self._mode == 'cut':
                    return
            env = self._parse(message)
            if env is None:
                await sink.send(message)
                continue
            rec = self._record(direction, env)
            out = message
            if direction == 'hub_to_agent' and env.get('type') == 'CHANGE_NOTIFICATION':
                handled = self._apply_notif_policy(rec)
                if handled == 'drop':
                    continue
                if handled == 'dup':
                    await sink.send(out)
                    dup_rec = self._record(direction, env)
                    dup_rec['duplicated'] = True
                    await sink.send(out)
                    self._event('change_notification_duplicated', rec)
                    continue
            if direction == 'hub_to_agent' and env.get('type') == 'SYNC_RESPONSE':
                mutated = self._apply_sync_mutation(rec, env)
                if mutated is not None:
                    out = json.dumps(mutated)
            await sink.send(out)

    def _apply_notif_policy(self, rec):
        with self._lock:
            mode = self._notif_mode
            target = self._notif_dup_target
            oid = rec.get('object_id')
            if mode == 'drop':
                self._event('change_notification_dropped', rec)
                return 'drop'
            if mode == 'dup' and target and oid == target and oid not in self._notif_dup_done:
                self._notif_dup_done.add(oid)
                return 'dup'
        return 'pass'

    def _apply_sync_mutation(self, rec, env):
        with self._lock:
            mut = self._sync_mutation
            if not mut or self._sync_mutation_done:
                return None
            self._sync_mutation_done = True
        p = env.get('payload') or {}
        mode = mut.get('mode')
        detail = {'mode': mode, 'at': time.time(), 'at_utc': now_utc(),
                  'global_rev': p.get('global_rev'), 'seq': rec.get('seq')}
        if mode == 'bad_sha':
            sid = mut['script_id']
            for e in p.get('scripts') or []:
                if e.get('id') == sid and isinstance(e.get('data'), dict):
                    real = e['data'].get('sha256')
                    e['data']['sha256'] = 'deadbeef' * 8
                    detail.update({'script_id': sid, 'sha256_original': real,
                                   'sha256_injected': e['data']['sha256']})
        elif mode == 'rollback_in_tx':
            phantom = dict(mut['phantom_script'])
            phantom['data'] = dict(phantom['data'])
            p.setdefault('scripts', []).append(phantom)
            for e in p.get('tasks') or []:
                if e.get('id') == mut['task_id']:
                    e['data'] = '<<malformed-json-not-an-object>>'
                    detail['malformed_task_id'] = mut['task_id']
            detail['phantom_script_id'] = phantom['id']
        elif mode == 'drop_dependency':
            sid = mut['script_id']
            before = len(p.get('scripts') or [])
            p['scripts'] = [e for e in (p.get('scripts') or []) if e.get('id') != sid]
            detail.update({'script_id': sid, 'removed': before - len(p['scripts'])})
        self._event('sync_response_mutated', detail)
        with self._lock:
            self._sync_mutations_applied.append(detail)
        return env

    def mutations_applied(self):
        with self._lock:
            return list(self._sync_mutations_applied)
