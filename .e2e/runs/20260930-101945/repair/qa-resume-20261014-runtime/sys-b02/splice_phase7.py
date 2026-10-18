#!/usr/bin/env python3
"""Splice the rewritten phase 7 block into run_sysb02.py (harness maintenance)."""
import io
import re
from pathlib import Path

P = Path(__file__).resolve().parent / 'run_sysb02.py'
src = P.read_text()
start = src.index('    # ---------------- phase 7: SYS-08 sync dependency failure ----------------')
end = src.index('    # ---------------- phase 8: AR-16 recovery')

NEW = '''    # ---------------- phase 7 helpers ----------------
    def latest_global_rev(self, object_id):
        snap = self.hub_snapshot()
        revs = [c['global_revision'] for c in snap['owned_changes'] if c['object_id'] == object_id]
        return max(revs) if revs else None

    def put_script(self, sid, body):
        cur = self.hub.json('/api/scripts/' + self.hub.quote(sid))
        payload = dict(cur)
        payload['content'] = body
        code, out = self.hub.put('/api/scripts/' + self.hub.quote(sid), payload)
        self.budget['api_mutations'] += 1
        if code != 200:
            raise RuntimeError('script_put_http_%s %s' % (code, out))
        return out

    def sync_ack_after_seq(self, seq, timeout=60):
        return wait_for(lambda: next((f for f in self.proxy.frame_summary()
                                      if f['type'] == 'SYNC_ACK' and f['seq'] > seq), None),
                        timeout=timeout, interval=0.4, label='sync_ack')

    def force_injected_sync(self, build_mutation, label, attempts=5):
        """Force one injected SYNC_RESPONSE and return the correlated observation.

        A foreign batch notification may consume the armed mutation before our own
        change is committed; the correlation on (mutated frame seq, committed
        revision of our object) makes that case detectable and the attempt is
        retried instead of being mis-reported.
        """
        attempts_used = []
        for attempt in range(1, attempts + 1):
            applied_before = len(self.proxy.mutations_applied())
            self.proxy.arm_sync_mutation(build_mutation())
            body = '%s# %s attempt=%d\n' % (BODY_GOOD, label, attempt)
            committed = self.put_script(self.state['script_good'], body)
            my_rev = self.latest_global_rev(self.state['script_good'])
            mut = wait_for(lambda: next((x for x in reversed(self.proxy.mutations_applied()[applied_before:])), None),
                           timeout=90, interval=0.4, label='sync_mutation_applied')
            record = {'attempt': attempt, 'committed_revision': committed.get('revision'),
                      'committed_global_revision_of_our_object': my_rev,
                      'mutation': mut, 'covers_our_commit': None, 'sync_ack': None}
            if not mut:
                attempts_used.append(record)
                continue
            record['covers_our_commit'] = bool(my_rev is not None and mut.get('global_rev') is not None
                                               and mut['global_rev'] >= my_rev)
            ack = self.sync_ack_after_seq(mut.get('seq', 0), timeout=60)
            record['sync_ack'] = None if not ack else {'ok': ack.get('ok'), 'applied_rev': ack.get('applied_rev'),
                                                      'error': ack.get('error'), 'seq': ack['seq'],
                                                      'at_utc': ack['at_utc']}
            attempts_used.append(record)
            time.sleep(1.5)
            if record['covers_our_commit'] and record['sync_ack'] is not None:
                return {'attempts': attempts_used, 'final': record,
                        'used_attempt': attempt, 'committed': committed}
        return {'attempts': attempts_used, 'final': attempts_used[-1] if attempts_used else None,
                'used_attempt': None, 'committed': None}

    # ---------------- phase 7: SYS-08 sync dependency failure ----------------
    def phase7_sync_failure(self):
        sid = self.state['script_good']
        dep = self.state['script_dep']
        tid = self.state['task_run']

        def local_state():
            return {
                'revision': self.child.db_query('SELECT global_revision FROM sync_state WHERE id=1')['rows'],
                'script_good': self.child.db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?', [sid])['rows'],
                'script_dep': self.child.db_query('SELECT id,revision,sha256 FROM scripts WHERE id=?', [dep])['rows'],
                'task_run': self.child.db_query('SELECT id,revision FROM tasks WHERE id=?', [tid])['rows'],
                'task_dep': self.child.db_query('SELECT id,revision FROM tasks WHERE id=?',
                                                [self.state['task_dep']])['rows'],
                'scripts_count': self.child.db_query('SELECT count(*) AS n FROM scripts')['rows'],
            }

        hub_before = self.hub_snapshot()
        child_before = local_state()
        hub_rev_before = hub_before['revision_sequence']
        local_rev_before = child_before['revision']

        # V1: corrupt the SHA of the script that a Task depends on inside one SYNC_RESPONSE
        r1 = self.force_injected_sync(lambda: {'mode': 'bad_sha', 'script_id': dep}, 'v1-bad-sha')
        self.budget['sync_responses_mutated'] += 1
        child_after_v1 = local_state()
        hub_after_v1 = self.hub_snapshot()
        node_v1 = self.node()
        v1 = {
            'variation': 'SYNC_RESPONSE with a corrupted sha256 on the Script referenced by a Task',
            'injection': r1,
            'sync_ack': (r1['final'] or {}).get('sync_ack'),
            'child_local_before': {'revision': local_rev_before, 'script_good': child_before['script_good'],
                                   'script_dep': child_before['script_dep'], 'task_run': child_before['task_run']},
            'child_local_revision_after': child_after_v1['revision'],
            'child_local_revision_unchanged': child_after_v1['revision'] == local_rev_before,
            'child_local_script_good': child_after_v1['script_good'],
            'child_local_script_dep': child_after_v1['script_dep'],
            'child_local_task_run': child_after_v1['task_run'],
            'no_half_sync': (child_after_v1['script_good'] == child_before['script_good']
                             and child_after_v1['script_dep'] == child_before['script_dep']
                             and child_after_v1['task_run'] == child_before['task_run']),
            'hub_global_revision_advanced': hub_after_v1['revision_sequence'] > hub_rev_before,
            'hub_committed_rows_for_object': [s for s in hub_after_v1['owned_scripts'] if s['id'] in (sid, dep)],
            'hub_rows_match_pre_failure': [s for s in hub_after_v1['owned_scripts'] if s['id'] in (sid, dep)]
                                          == [s for s in hub_before['owned_scripts'] if s['id'] in (sid, dep)],
            'node_readback': {k: node_v1.get(k) for k in ('status', 'sync_status', 'global_revision')} if node_v1 else None,
            'hub_agent_sync_state_for_child': [r for r in hub_after_v1['agent_sync_state']
                                               if r['node_id'] == self.state['child_node_id']],
        }

        # V2: in-transaction rollback - phantom script entry plus a malformed task entry
        phantom_id = str(uuid.uuid4())
        phantom_body = "#!/bin/sh\\necho phantom\\n"
        phantom = {'id': phantom_id, 'revision': 1, 'data': {
            'id': phantom_id, 'name': PREFIX + 'phantom', 'description': 'harness phantom entry',
            'interpreter': 'shell', 'content': phantom_body, 'sha256': sha256(phantom_body),
            'parameters': [], 'environment': {}, 'working_dir': '', 'timeout': 20, 'enabled': True,
            'revision': 1}}
        r2 = self.force_injected_sync(lambda: {'mode': 'rollback_in_tx', 'phantom_script': phantom,
                                               'task_id': self.state['task_dep']}, 'v2-in-tx-rollback')
        self.budget['sync_responses_mutated'] += 1
        child_after_v2 = local_state()
        hub_after_v2 = self.hub_snapshot()
        node_v2 = self.node()
        phantom_local = self.child.db_query('SELECT id,name,revision FROM scripts WHERE id=?', [phantom_id])['rows']
        v2 = {
            'variation': 'SYNC_RESPONSE carrying a valid phantom Script entry followed by a malformed Task '
                         'entry, so the Agent write transaction fails after the script write',
            'injection': r2,
            'phantom_script_id': phantom_id,
            'sync_ack': (r2['final'] or {}).get('sync_ack'),
            'phantom_script_absent_locally': len(phantom_local) == 0,
            'phantom_script_local_rows': phantom_local,
            'child_local_revision_after': child_after_v2['revision'],
            'child_local_revision_unchanged': child_after_v2['revision'] == local_rev_before,
            'child_local_script_good': child_after_v2['script_good'],
            'child_local_task_dep': child_after_v2['task_dep'],
            'child_local_scripts_count': child_after_v2['scripts_count'],
            'no_half_sync': (child_after_v2['script_good'] == child_before['script_good']
                             and child_after_v2['script_dep'] == child_before['script_dep']),
            'node_readback': {k: node_v2.get(k) for k in ('status', 'sync_status', 'global_revision')} if node_v2 else None,
            'hub_agent_sync_state_for_child': [r for r in hub_after_v2['agent_sync_state']
                                               if r['node_id'] == self.state['child_node_id']],
        }

        # V3 (informational): the referenced dependency is absent from the snapshot
        r3 = self.force_injected_sync(lambda: {'mode': 'drop_dependency', 'script_id': dep}, 'v3-drop-dependency')
        self.budget['sync_responses_mutated'] += 1
        time.sleep(3.0)
        dep_after = self.child.db_query('SELECT id,revision FROM scripts WHERE id=?', [dep])['rows']
        v3 = {
            'variation': 'the Script referenced by a Task is absent from the SYNC_RESPONSE snapshot',
            'injection': r3,
            'sync_ack': (r3['final'] or {}).get('sync_ack'),
            'dependency_script_present_locally_after': bool(dep_after),
            'dependency_local_rows': dep_after,
            'observation': 'the Agent applies the Hub snapshot as the authority for pruned objects and has no '
                           'cross-object dependency validation; a snapshot that omits a task-referenced script is '
                           'accepted (the local script is pruned) and the next authoritative snapshot restores it. '
                           'Reported as an observation; the subsequent phase re-syncs and re-checks the row.',
        }
        out = {
            'at_utc': now_utc(),
            'hub_revision_before': hub_rev_before,
            'child_local_revision_before': local_rev_before,
            'v1_bad_sha': v1,
            'v2_rollback_in_tx': v2,
            'v3_missing_dependency_observation': v3,
            'assertions_pass': bool(
                v1['sync_ack'] and v1['sync_ack']['ok'] is False
                and v1['child_local_revision_unchanged'] and v1['no_half_sync']
                and v2['sync_ack'] and v2['sync_ack']['ok'] is False
                and v2['phantom_script_absent_locally']
                and v2['child_local_revision_unchanged'] and v2['no_half_sync']),
        }
        self.ev('phase7-sys08-sync-failure-rollback.json', out)
        return out

'''
src = src[:start] + NEW + src[end:]
P.write_text(src)
print('spliced', len(NEW), 'chars; file now', len(src))
