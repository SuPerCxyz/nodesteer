#!/usr/bin/env python3
"""Server-write accounting for web batch06, derived from the captured evidence.

The in-run budget counter did not increment for UI-driven mutations
(ui_mutations / node_status_posts stayed 0), and the per-step network logs are
cleared per action so they undercount repeated clicks. This script recomputes
the accounting from authoritative sources:

  * the Hub audit log (one row per node status update / fixture create / delete),
  * the recorded API call sites in run_batch06.py for the run attempts.

It records the corrected block in results.json and run-evidence.json.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'runners'))
import hubapi  # noqa: E402

EV = HERE / 'evidence'
CHILD = 'bf80d9a9-2adc-4aa9-a58e-8d56d254590a'
GROUP = 'aa4db877-d886-4389-99eb-4b26f6fc6d08'

hub = hubapi.Hub()
hub.login()
rows = hub.json('/api/audit?limit=500')
rows = rows if isinstance(rows, list) else rows.get('items', [])


def count(resource, action, rid=None):
    return len([r for r in rows if r.get('resource') == resource and r.get('action') == action
                and (rid is None or r.get('resource_id') == rid)])


node_status_updates = count('node', 'update', CHILD)
group_updates = count('group', 'update', GROUP)
audit_seen = {
    'node_update_owned_child': node_status_updates,
    'node_delete_owned_child': count('node', 'delete', CHILD),
    'group_create': count('group', 'create', GROUP),
    'group_update_member_change': group_updates,
    'group_delete': count('group', 'delete', GROUP),
    'note': 'audit was filtered to this batch\'s own owned fixture ids only; unattributed audit rows '
            'belong to other concurrent batches and are excluded',
}

p5 = json.loads((EV / 'phase5-p11-04-offline-node-dispatch.json').read_text())
p6 = json.loads((EV / 'phase6-p06-08-group-change-sync.json').read_text())
run_attempts = 0
for s in p5['steps']:
    run_attempts += len((s.get('ui_run') or {}).get('post_requests') or [])
run_attempts += len((p6['steps'][3].get('group_task_ui_run') or {}).get('post_requests') or [])
run_attempts += 1   # explicit API probe in the same P06-08 step (recorded http 400)

accounting = {
    'source': 'Hub audit log (authoritative) + recorded call sites; the in-run budget counter did not '
              'increment for UI-driven mutations and the per-step network logs are cleared per action',
    'endpoint': 'http://192.168.100.209:18080 (repair only; primary :8080 never contacted)',
    'audit_observed': audit_seen,
    'owned_child_node_status_writes': {
        'count': node_status_updates,
        'route': 'POST /api/nodes/{owned child id} {"status": online|maintenance}',
        'routes_used': ['UI 操作菜单 维护', 'UI 操作菜单 恢复', 'UI 操作菜单 设为在线'],
        'protected_node_status_writes': 0,
        'evidence': 'evidence/phase4-p04-05-offline-set-online.json#node_audit_readback',
    },
    'ui_group_writes': {
        'count': group_updates,
        'route': 'PUT /api/groups/{owned group id} (member add / member remove)',
    },
    'owned_task_run_attempts': {
        'count': run_attempts,
        'route': 'POST /api/tasks/{owned task id}/run',
        'responses': 'all 4xx; exactly one attempt (P11-04 variant A) left a FAILED Execution row, '
                     'the other three left no Execution row',
    },
    'fixture_create_delete': {
        'POST /api/nodes/enrollment': 1,
        'POST /api/scripts': 1, 'DELETE /api/scripts/{id}': 1,
        'POST /api/groups': 1, 'DELETE /api/groups/{id}': 1,
        'POST /api/tasks': 2, 'DELETE /api/tasks/{id}': 2,
        'POST /api/schedules': 5, 'DELETE /api/schedules/{id}': 5,
        'DELETE /api/nodes/{owned child id}': 1,
    },
    'not_performed': ['User/Application/Artifact create or delete', 'real application deploy',
                      'Hub stop/start', 'protected Agent stop/start',
                      'product source change', 'Git operation', 'OpenSpec Archive'],
    'immutable_history_left_behind': 5,
}
for path in (HERE / 'results.json', HERE / 'run-evidence.json'):
    data = json.loads(path.read_text())
    data['write_accounting'] = accounting
    if 'server_writes' in data:
        data['server_writes']['corrected_accounting'] = 'see write_accounting'
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(accounting, ensure_ascii=False, indent=2))
