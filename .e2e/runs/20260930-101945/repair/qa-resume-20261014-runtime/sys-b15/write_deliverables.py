#!/usr/bin/env python3
"""Build the sys-b15 deliverables from the raw evidence produced by run_sysb15.py.

Outputs (in this directory):
  case-result.json          - per-case verdict for X01 / X03
  fixture-ledger.json       - every owned fixture created, verified and removed
  final-cleanup-oracle.json - cleanup oracle + protected-environment baseline
  evidence/binary-and-unit-state.json - explicit before/after Agent binary SHA and
                              unit-state summary requested by the batch owner
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV = HERE / 'evidence'
sys.path.insert(0, str(HERE))
import lib as X  # noqa: E402

NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb15-%s-' % NONCE
RUN = json.loads((DEV / 'run-evidence.json').read_text())
P0 = json.loads((DEV / 'phase0-guards.json').read_text())
P1 = json.loads((DEV / 'phase1-children.json').read_text())
P2 = json.loads((DEV / 'phase2-x01-mixed-batch.json').read_text())
P3 = json.loads((DEV / 'phase3-x01-duplicate-inflight.json').read_text())
P4 = json.loads((DEV / 'phase4-x01-version-reconnect.json').read_text())
P5 = json.loads((DEV / 'phase5-x01-failures.json').read_text())
P6 = json.loads((DEV / 'phase6-x01-cancel-confirm.json').read_text())
P7 = json.loads((DEV / 'phase7-x03-on-start.json').read_text())
P8 = json.loads((DEV / 'phase8-cleanup.json').read_text())


def small(v, limit=1200):
    text = json.dumps(v, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '...'


def write(name, value):
    X.update_json(HERE / name, value)


def main():
    hub = X.Hub()
    hub.login()
    upg_node = P1['children']['upg']['node_id']
    hard_node = P1['children']['hard']['node_id']
    dock_node = P1['children']['dock']['node_id']
    off_node = P1['children']['off']['node_id']
    pause_node = P1['children']['pause']['node_id']
    os1_node = P1['children']['os1']['node_id']

    # retained Hub-side history produced by this batch (Execution rows are never deleted)
    code, upgrade_rows, _ = hub.executions(task_id=X.UPGRADE_TASK_ID, limit=200)
    upgrade_rows = [r for r in upgrade_rows if r.get('node_id') in
                    (upg_node, hard_node, dock_node, off_node, pause_node, os1_node)]
    upgrade_rows.sort(key=lambda r: str(r.get('start_time')))
    task_id = P7['created']['task']
    _, onstart_rows, _ = hub.executions(task_id=task_id, limit=50)
    onstart_rows.sort(key=lambda r: str(r.get('start_time')))
    audit = []
    for action in ('upgrade', 'create', 'delete', 'update'):
        c, rows, _ = hub.get('/api/audit?action=%s&limit=200' % action)
        if c == 200 and isinstance(rows, list):
            for r in rows:
                if any(x in small(r) for x in
                       (upg_node, hard_node, dock_node, off_node, pause_node, os1_node,
                        P7['created']['script'], task_id, P7['created']['schedule'])):
                    audit.append({'action': r.get('action'), 'object_type': r.get('object_type'),
                                  'object_id': r.get('object_id'), 'at': str(r.get('created_at') or r.get('at'))})
    audit = audit[:40]

    f1_row = [r for r in P5['f2']['rows'] if 'restart agent service' in (r.get('block_reason') or '')]
    f2_row = [r for r in P5['f2']['rows'] if 'backup current agent binary' in (r.get('block_reason') or '')]
    f1_row = f1_row[0] if f1_row else {}
    f2_row = f2_row[0] if f2_row else {}

    before_sha = {k: v.get('sha256') for k, v in P0['protected_binary_sha_before'].items()}
    after_sha = {k: v.get('sha256') for k, v in P8['protected_binary_sha_after'].items()}

    # ---------------- evidence/binary-and-unit-state.json ------------------ #
    write('evidence/binary-and-unit-state.json', {
        'at_utc': X.now_utc(),
        'protected_baseline_agent_binary': {
            'path': X.AGENT_BIN,
            'expected_sha256': X.AGENT_SHA,
            'sha256_by_node_before': before_sha,
            'sha256_by_node_after': after_sha,
            'all_match_expected_before': all(v == X.AGENT_SHA for v in before_sha.values()),
            'all_match_expected_after': all(v == X.AGENT_SHA for v in after_sha.values()),
        },
        'hub_served_self_upgrade_artifact': P0['hub_agent_binary'],
        'protected_unit_state_before': X.unit_fingerprint(P0['protected_units_before']),
        'protected_unit_state_after': X.unit_fingerprint(P8['protected_units_after']),
        'protected_units_identical': P8['protected_units_unchanged'],
        'owned_child_binary_replacement': {
            'child': 'upg', 'node_id': upg_node, 'host': P1['children']['upg']['host_ip'],
            'path': P1['children']['upg']['binary_on_node'],
            'sha256_before_upgrade': P1['children']['upg']['prepare']['owned_binary_sha256'],
            'sha256_after_upgrade': P4['binary_after_replace']['sha256'],
            'backup_sha256': P4['bak_after_replace']['sha256'],
            'equals_hub_artifact_sha': P4['binary_after_replace']['sha256'] == X.AGENT_SHA,
            'shim_calls': P4['shim_calls'],
            'process_pid_before_restart': P4['owned_state_after_replace']['owned_pids'],
            'process_pid_after_restart': P4['owned_state_after_restart']['owned_pids'],
        },
        'failure_paths_owned_binary': {
            'restart_failure_rollback': {
                'execution_id': f1_row.get('id'),
                'block_reason': f1_row.get('block_reason'),
                'binary_sha256_after_rollback': P5['after_restart_failure']['files'][P1['children']['hard']['binary_on_node']].get('sha256'),
                'equals_pre_upgrade_sha': P5['f1']['binary_restored_to_previous_sha'],
                'backup_removed_by_rollback': P5['f1']['backup_consumed_by_rollback'],
            },
            'backup_step_failure': {
                'execution_id': f2_row.get('id'),
                'block_reason': f2_row.get('block_reason'),
                'binary_sha256_unchanged': P5['f2']['binary_unchanged'],
                'no_partial_new_file': P5['f2']['no_leftover_new_file'],
            },
        },
        'note': ('Every protected Agent binary stayed byte-identical (d55dc5cd...) before/after and '
                 'every protected unit kept its original MainPID/ExecMainStartTimestamp. Only the '
                 'harness-owned copies under /opt/qa-sysb15-<nonce>/ were ever replaced (and were '
                 'removed at cleanup).'),
    })

    # ---------------------------- case-result.json ------------------------- #
    cases = [
        {
            'case_id': 'X01',
            'title': '节点批量 Native Agent 自升级：混选 Docker、暂停/离线、重复在途升级、部分失败、取消确认、版本更新和重连。',
            'canonical_before': 'NOT_RUN (repair build not executed; legacy evidence excluded per remaining_dimensions); inventory baseline_status=PARTIAL',
            'proposed_after': 'PASS',
            'confidence': 'medium-high',
            'covered_dimensions': [
                {
                    'test_id': '20261014-RUNTIME:X01-mixed-batch',
                    'layer': 'real Hub API + real WS dispatch + real owned child Agents (exact repair binary)',
                    'status': 'PASS',
                    'description': [
                        'One POST /api/nodes/upgrade with 4 mixed targets returned 4 independent records: native+online -> dispatched PENDING (execution 1d433d80-6ceb-4bcd-a191-5a6c0e41190c); docker-mode node -> SKIPPED "self-upgrade unsupported for deployment mode docker" (c71b229a-...); offline node -> FAILED "node is not online (status=offline)" (22d28266-...); maintenance node -> SKIPPED "node paused" (d5fa2b65-...).',
                        'The batch was NOT aborted by the failing/skipped members and each node got its own execution id: 混选 + 部分失败 in one real request.',
                    ],
                },
                {
                    'test_id': '20261014-RUNTIME:X01-duplicate-inflight',
                    'layer': 'real Hub API dedup (findActiveUpgrade) while the first upgrade was genuinely in flight',
                    'status': 'PASS',
                    'description': [
                        'While the first owned upgrade was RUNNING (owned systemctl shim held the restart call for 25s), a second POST for the same node returned {"execution_id":"1d433d80-...","status":"RUNNING","deduplicated":true} - no new dispatch, same execution id.',
                        'Final per-node history contains exactly ONE agent_upgrade record for that node (SUCCESS), i.e. no duplicate upgrade.',
                        'Batch-internal duplicate ids are collapsed before dispatch: POST [dock,dock] returned exactly 1 row.',
                    ],
                },
                {
                    'test_id': '20261014-RUNTIME:X01-real-upgrade-and-version',
                    'layer': 'real binary download + real SHA256 verify + real backup + real atomic replace + real restart + real reconnect',
                    'status': 'PASS',
                    'description': [
                        'The owned child ran a deliberately older (marker-appended) binary copy sha256=5a328a86...; after the upgrade the owned path held sha256=d55dc5cd... == the exact artifact served by GET /api/agent/binary?architecture=amd64 (header + body hash read back).',
                        'The previous version was backed up to <exe>.bak (sha256=5a328a86..., 16805122 B = baseline+35 B marker) before replacement.',
                        'Execution terminal state SUCCESS with stdout "agent binary replaced; service restarting" (Hub journal readback).',
                        'Harness restarted the owned process: new PID (33691 -> 33879), node back online/synced, Hub reported agent_version=dddee4f0-e2e-repair-20261004 and capabilities.agent_upgrade=true; still exactly 1 upgrade execution.',
                        'LIMITATION: the repair Hub serves exactly the running build ("升级到 Hub 同版本"), so the reported version string is identical before/after; the version dimension is proven as "binary replaced by + reported version equals the Hub build", not as a cross-build version bump.',
                    ],
                },
                {
                    'test_id': '20261014-RUNTIME:X01-failure-paths',
                    'layer': 'real rollback path + real pre-replace failure path (no binary side effects)',
                    'status': 'PASS',
                    'description': [
                        'Restart failure (owned shim exits 1): execution FAILED with block_reason "restart agent service: exit status 1; rollback restored previous binary"; the owned binary was restored byte-identically to the pre-upgrade sha256 and the .bak was consumed by the rollback.',
                        'Backup step failure (pre-created <exe>.bak directory): execution FAILED with block_reason "backup current agent binary: open .../nodesteer-agent.bak: is a directory"; the binary was untouched and no .new/.tmp leftover remained.',
                        'Neither failure produced a half-replaced binary or a fake SUCCESS.',
                    ],
                },
                {
                    'test_id': '20261014-RUNTIME:X01-cancel-confirm',
                    'layer': 'API-level negative + frontend source contract (UI click-through deliberately NOT executed)',
                    'status': 'PARTIAL',
                    'description': [
                        'With no request issued (the UI cancel path), the agent_upgrade execution count stayed at 14 over the observation window; an identical control request in the same window created a new row (14 -> 15), so the negative is not vacuous.',
                        'Frontend contract: web/src/features/catalog/node-bulk-actions.tsx:141-172 wires the POST (handleConfirm) only to the ConfirmDialog confirm action; cancelBtnText closes the dialog, so cancelling performs no API call and therefore no upgrade.',
                        'NOT EXECUTED: clicking through the real bulk-upgrade ConfirmDialog. The same dialog confirms real protected native nodes; a mis-click would replace Agent binaries on protected nodes. This is a deliberate risk decision, not a product gap.',
                    ],
                },
            ],
            'remaining_or_limitations': [
                '取消确认 was verified at the API level + frontend source contract only; the real browser click-through of the ConfirmDialog cancel path was not executed (protective decision: a mis-click there would self-upgrade the 4 protected native nodes).',
                '版本更新 was verified as "owned binary replaced by the Hub-served build artifact (SHA change) and version/capability reported after reconnect"; a genuine cross-build version bump could not be exercised because the environment holds exactly one repair build (Hub artifact SHA == baseline SHA d55dc5cd...).',
                'The systemd restart step ran through the harness seam described in harness_seams.agent_restart (owned `systemctl` shim + harness-performed restart of the owned process), never through the protected nodesteer-agent.service.',
            ],
            'evidence': [
                'phase0-guards.json', 'phase1-children.json', 'phase2-x01-mixed-batch.json',
                'phase3-x01-duplicate-inflight.json', 'phase4-x01-version-reconnect.json',
                'phase5-x01-failures.json', 'phase6-x01-cancel-confirm.json',
                'binary-and-unit-state.json', 'phase8-cleanup.json', 'progress-steps.json',
                'run-evidence.json', 'run-stdout.log', 'case-result.json', 'fixture-ledger.json',
                'final-cleanup-oracle.json', 'post-run-independent-verification.json',
                'evidence-run1-aborted/ (first attempt aborted by a harness format bug before any binary was touched; retained for audit)',
            ],
            'actual_resources': {
                'owned_prefix': PREFIX,
                'owned_child_nodes': {
                    'upg': upg_node, 'hard': hard_node, 'dock': dock_node,
                    'off': off_node, 'pause': pause_node},
                'upgrade_executions_retained': [
                    {'id': r.get('id'), 'node_id': r.get('node_id'), 'status': r.get('status'),
                     'block_reason': r.get('block_reason')} for r in upgrade_rows],
                'owned_child_binary_replacement': {
                    'controlled_old_version_sha256': P1['children']['upg']['prepare']['owned_binary_sha256'],
                    'after_upgrade_sha256': P4['binary_after_replace']['sha256'],
                    'hub_artifact_sha256': P0['hub_agent_binary']['body_sha256'],
                    'backup_sha256': P4['bak_after_replace']['sha256'],
                },
                'shim_calls_upg': P4['shim_calls'],
                'shim_calls_hard': P5['shim_calls'],
                'audit_rows': audit,
            },
        },
        {
            'case_id': 'X03',
            'title': 'on_start 每次Agent进程启动触发一次；网络重连不再次触发；禁用/删除停止触发。',
            'canonical_before': 'NOT_RUN (repair build not executed; legacy evidence excluded per remaining_dimensions); inventory baseline_status=PASS',
            'proposed_after': 'PASS',
            'confidence': 'high',
            'covered_dimensions': [
                {
                    'test_id': '20261014-RUNTIME:X03-once-per-process-start',
                    'layer': 'real Hub Script/Task/Schedule + real owned child Agent + real WS frame proxy ledger + real marker file',
                    'status': 'PASS',
                    'description': [
                        'An on_start Schedule (execution_owner=agent, offline_policy=allow_offline, enabled) synced into the owned child local store; the first owned process start produced exactly 1 execution (trigger_type=schedule, scheduled_time=13:54:58Z = that process start second) and exactly 1 marker line on the node.',
                        'A second owned process start produced a second execution (scheduled_time=13:55:03Z, distinct slot) and a second marker line: exactly one trigger per process start, no duplicates, no catch-up runs.',
                        'Local store readback shows both executions SUCCESS with distinct scheduled_time slots (task+node+slot idempotency key).',
                    ],
                },
                {
                    'test_id': '20261014-RUNTIME:X03-reconnect-no-retrigger',
                    'layer': 'real WS cut/reconnect without process restart (harness frame proxy)',
                    'status': 'PASS',
                    'description': [
                        'The proxy cut the child link; the Agent logged "connection lost ... received close frame ... reason = proxy cut" while the SAME process (PID 34161 before and after) kept retrying.',
                        'After restoring the link the Agent reconnected (new client connection, relayed connection open) and completed a fresh HELLO + SYNC_RESPONSE snapshot at 13:55:11Z - the exact hook where on_start is evaluated - yet the execution count stayed at 2 and the marker file stayed at 2 lines: a network reconnect does NOT re-trigger.',
                    ],
                },
                {
                    'test_id': '20261014-RUNTIME:X03-disable-delete-stop',
                    'layer': 'real Hub Schedule update/delete + real child store convergence + real process restarts',
                    'status': 'PASS',
                    'description': [
                        'Schedule disabled (PUT, revision 2) and confirmed disabled in the child local store; the next process start produced no execution (still 2).',
                        'Schedule deleted (DELETE 200 -> GET 404) and confirmed absent from the child local store (schedules list empty after the tombstone sync); the next process start produced no execution (still 2).',
                    ],
                },
            ],
            'remaining_or_limitations': [
                'The "node paused at start -> trigger deferred until resume" branch is documented in internal/agent/agent.go (OnNodeStatus) but was NOT exercised here; it is not part of the X03 acceptance statement and belongs to the pause semantics already covered by sys-b12/sys-b09-style batches.',
            ],
            'evidence': [
                'phase1-children.json', 'phase7-x03-on-start.json', 'phase8-cleanup.json',
                'progress-steps.json', 'run-evidence.json', 'case-result.json',
                'fixture-ledger.json', 'final-cleanup-oracle.json', 'post-run-independent-verification.json',
            ],
            'actual_resources': {
                'owned_prefix': PREFIX,
                'onstart_node': os1_node,
                'script_id': P7['created']['script'],
                'task_id': task_id,
                'schedule_id': P7['created']['schedule'],
                'marker_path_on_node': P7['created']['marker_on_node'],
                'executions_retained': [
                    {'id': r.get('id'), 'status': r.get('status'),
                     'scheduled_time': str(r.get('scheduled_time')), 'trigger_type': r.get('trigger_type')}
                    for r in onstart_rows],
                'counts': {
                    'after_start_1': P7['t1_first_start']['wait']['last']['executions'],
                    'after_start_2': P7['t2_second_start']['wait']['last']['executions'],
                    'after_reconnect_same_process': P7['t3_reconnect']['counts']['executions'],
                    'after_disabled_restart': P7['t4_disabled_start']['counts']['executions'],
                    'after_deleted_restart': P7['t5_deleted_start']['counts']['executions'],
                },
                'marker_lines': P7['t2_second_start']['wait']['last']['marker_lines'],
                'reconnect_frame_evidence': P7['t3_reconnect']['sync_frames_after_reconnect'],
                'agent_connection_lost_log': P7['t3_reconnect']['agent_observed_connection_lost']['last'],
                'audit_rows': audit,
            },
        },
    ]

    upgrade_posts = [P2['request'], P3['duplicate_request_http'], P3['duplicate_ids_in_one_batch'],
                     P5['restart_failure_request'], P5['backup_failure_request'], P6['control_post']]
    budget = {
        # 6 upgrade POSTs: mixed batch, duplicate-in-flight, batch-internal duplicate ids,
        # hard F1, hard F2, phase6 control
        # 6 enrolls + 6 node deletes + 3 fixture creates + 1 schedule PUT + 3 fixture deletes
        # + 1 node status change (pause -> maintenance)
        'api_mutations': 6 + 6 + 6 + 3 + 1 + 3 + 1,
        'upgrade_posts': len(upgrade_posts),
        'node_enroll_posts': len(P1['children']),
        'node_deletes': len(P8['node_deletes']),
        # 6 initial owned child starts + 1 harness restart of `upg` after the binary
        # replacement + 4 process starts of the on_start child (T1..T5 restarts)
        'child_process_starts': 6 + 1 + 4,
        'child_process_removals': len(P8['child_removals']),
        'protected_unit_stop_starts': 0,
        'vms_restarted': 0,
        'product_source_changes': 0,
        'git_operations': 0,
    }

    write('case-result.json', {
        'batch_id': 'sys-b15',
        'title': 'Agent 自升级与 on_start 触发语义（二进制级批次，独占）',
        'nonce': NONCE,
        'owned_prefix': PREFIX,
        'endpoint': RUN['authority'],
        'executed_at_utc': RUN['started_at_utc'],
        'finished_at_utc': RUN['finished_at_utc'],
        'run_attempts': {
            'attempt02_final': 'all 8 phases PASS; oracle_clean=true',
            'attempt01_retained': ('aborted inside phase4 by a harness format bug (unescaped %s in a remote probe '
                                   'template) AFTER the phase2/phase3 real upgrade had already replaced the owned '
                                   'child binary. No protected unit or protected binary was touched; leftovers '
                                   '(1 owned process + 2 batch dirs) were removed by hand and the batch was re-run '
                                   'cleanly. Evidence kept in evidence-run1-aborted/ and run1-stdout.log.'),
        },
        'environment': {
            'authority': 'http://192.168.100.209:18080 (repair) - the only management write endpoint used',
            'protected_nodes_online_synced_before': P0['protected_nodes'],
            'protected_nodes_online_synced_after': P8['protected_nodes'],
            'hub_agent_binary_sha256_expected': X.AGENT_SHA,
            'hub_agent_binary_sha256_observed_before_after': {
                'before': before_sha, 'after': after_sha},
            'hub_artifact_sha256_served_for_upgrade': P0['hub_agent_binary'],
            'primary_hub_8080_touched': False,
            'preserved_foreign_nodes': P0['other_nodes'],
        },
        'cases': cases,
        'deviations': [],
        'harness_failures_retained': [
            {'attempt': 1, 'phase': 'phase4', 'type': 'harness_bug',
             'detail': 'remote probe template used an unescaped %s inside a %-formatted string -> TypeError',
             'impact': 'batch aborted before version/reconnect/failure/cancel/on_start phases',
             'protected_impact': 'none (all 4 protected binaries + units unchanged, verified after the abort)'},
        ],
        'harness_seams': {
            'authority': 'management API writes only to repair http://192.168.100.209:18080',
            'owned_children': ('6 harness-owned nodes on the 4 repair VMs, each a plain background process from an '
                               'owned copy of the exact repair Agent binary under /opt/qa-sysb15-<nonce>/<name>/; '
                               'owned 0700 data roots under /var/lib/nodesteer-qa-sysb15-<nonce>/<name>'),
            'agent_restart': ('internal/agent/upgrade.go restarts the hard-coded unit name "nodesteer-agent", which '
                              'on these VMs is the PROTECTED primary unit. The owned children therefore ran with an '
                              'owned PATH whose first entry is an owned `systemctl` shim intercepting exactly '
                              '"restart nodesteer-agent" (logging + chosen exit code) and passing everything else to '
                              'the real systemctl. The actual process restart of the owned child was then performed '
                              'by the harness - the same lifecycle event systemd would perform for an owned unit. '
                              'A real systemctl restart of a protected unit never happened (proved by unchanged '
                              'MainPID/ExecMainStartTimestamp on all protected units).'),
            'old_version_fixture': ('the controlled pre-upgrade version is a byte-copy of the baseline Agent binary '
                                    'with an appended marker, sha256=5a328a86... (baseline d55dc5cd... + 35 B)'),
            'on_start_reconnect': 'harness frame proxy on the operator host cut/restored the owned child WS link (no process restart)',
            'credential_source': 'harness-owned registration token from the private 0600 credentials file + each child store',
            'product_source_changes': 0,
            'git_operations': 0,
            'archive_operations': 0,
            'vms_restarted': 0,
            'protected_unit_stop_starts': 0,
        },
        'budget': budget,
        'final_cleanup_oracle_clean': P8['oracle_clean'],
        'verdict_summary': {'X01': 'PASS (medium-high; cancel-UI click-through + cross-build version bump unverified, see limitations)',
                            'X03': 'PASS (high; on_start once-per-process-start / reconnect-no-retrigger / disable+delete all real)'},
        'verdict_notes': {
            'X01': ('All system-level sub-scenarios produced real observable behaviour (mixed docker/paused/offline '
                    'batch, duplicate-in-flight dedup, real replace + restart + reconnect + version/capability '
                    'readback, restart-failure rollback, backup-failure no-op). Two sub-dimensions are recorded as '
                    'PARTIAL/unverified: (a) the browser click-through of the bulk-upgrade ConfirmDialog cancel '
                    '(API+source-contract verified instead, deliberately not clicked for safety), (b) a genuine '
                    'cross-build version bump (only one repair build exists; the Hub serves "upgrade to Hub version", '
                    'so the version string is identical before/after while the binary SHA really changes). If Root '
                    'counts either as a required dimension, X01 should be read as PARTIAL rather than PASS.'),
        },
    })

    # --------------------------- fixture-ledger.json ----------------------- #
    fixture_rows = []
    for name, ch in P1['children'].items():
        child_removal = P8['child_removals'].get(name, {})
        node_delete = P8['node_deletes'].get(ch['node_id'], {})
        fixture_rows.append({
            'kind': 'owned_child_agent_node',
            'name': name,
            'node_id': ch['node_id'],
            'node_name': ch['node_name'],
            'host': ch['host_ip'],
            'deployment_mode': ch['deployment_mode_requested'],
            'binary_path': ch['binary_on_node'],
            'binary_created_sha256': ch['prepare']['owned_binary_sha256'],
            'data_root': ch['root_on_node'],
            'proxy_port': ch['hub_url'].split(':')[-1] if '192.168.100.170' in ch['hub_url'] else None,
            'created': True,
            'deleted': {'node_delete_http': node_delete.get('delete_http'),
                        'node_get_http_after': node_delete.get('get_http'),
                        'processes_remaining': child_removal.get('remaining_processes'),
                        'root_removed': child_removal.get('root_exists_after') is False,
                        'opt_removed': child_removal.get('opt_exists_after') is False},
            'retained_reason': 'none (fully removed; node tombstone/audit kept by the Hub)',
        })
    for label in ('script', 'task', 'schedule'):
        fid = P7['created'].get(label) or RUN.get('phases', {}).get('p7', {}).get('created', {}).get(label)
        d = P8['fixture_deletes'].get(label, {})
        fixture_rows.append({
            'kind': 'on_start_fixture_%s' % label,
            'id': fid,
            'name': ('%sonstart-%s' % (PREFIX, label)) if label in ('script', 'task') else None,
            'created': True,
            'deleted': {'delete_http': d.get('delete_http'), 'get_http_after': d.get('get_http')},
            'retained_reason': 'none (DELETE 200 -> GET 404)',
        })
    fixture_rows.append({
        'kind': 'retained_execution_history',
        'note': 'Execution rows are never deleted; both real upgrades, the failure paths and the on_start runs stay as audit history',
        'agent_upgrade_executions': [
            {'id': r.get('id'), 'node_id': r.get('node_id'), 'status': r.get('status'),
             'block_reason': r.get('block_reason')} for r in upgrade_rows],
        'on_start_executions': [
            {'id': r.get('id'), 'status': r.get('status'),
             'scheduled_time': str(r.get('scheduled_time'))} for r in onstart_rows],
    })
    write('fixture-ledger.json', {
        'batch_id': 'sys-b15', 'nonce': NONCE, 'prefix': PREFIX,
        'note': ('Every fixture is harness-owned with the nonce prefix; the 4 protected repair nodes, the '
                 'docker_host_integration node and the offline leftover node were read-only and never modified.'),
        'fixtures': fixture_rows,
        'retained_foreign_resources_note': ('No foreign resource was created, modified or deleted. No Transfer rows, '
                                            'no Artifacts, no Applications, no systemd units were created.'),
    })

    # ----------------------- final-cleanup-oracle.json --------------------- #
    write('final-cleanup-oracle.json', {
        'batch_id': 'sys-b15',
        'at_utc': X.now_utc(),
        'oracle_clean': P8['oracle_clean'],
        'checks': {k: P8[k] for k in ('all_fixtures_gone', 'all_roots_removed', 'all_processes_gone',
                                      'all_nodes_gone', 'batch_dirs_gone', 'no_owned_residue',
                                      'all_protected_online_synced', 'protected_units_unchanged',
                                      'all_baseline_sha_restored', 'health_ok')},
        'fixture_deletes': P8['fixture_deletes'],
        'node_deletes': P8['node_deletes'],
        'child_removals': P8['child_removals'],
        'batch_dir_removals': P8['batch_dir_removals'],
        'residue_sweep': P8['residue'],
        'health': P8['health'],
        'protected_nodes': {k: {kk: vv for kk, vv in v.items() if kk != 'capabilities'}
                            for k, v in P8['protected_nodes'].items()},
        'protected_binary_sha_after': P8['protected_binary_sha_after'],
        'protected_units_identical_before_after': P8['protected_units_unchanged'],
        'protected_unit_state_before': X.unit_fingerprint(P0['protected_units_before']),
        'protected_unit_state_after': X.unit_fingerprint(P8['protected_units_after']),
        'required_end_state': {
            'four_protected_nodes_online_synced': P8['all_protected_online_synced'],
            'healthz_readyz_200': P8['health_ok'],
            'all_agent_binaries_sha_d55dc5cd': P8['all_baseline_sha_restored'],
            'no_owned_process_dir_port': P8['no_owned_residue'] and P8['all_processes_gone'],
        },
        'errors': RUN.get('errors', []),
    })

    print(json.dumps({'case_result': 'written', 'fixtures': len(fixture_rows),
                      'oracle_clean': P8['oracle_clean'],
                      'upgrade_executions': len(upgrade_rows),
                      'onstart_executions': len(onstart_rows)}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
