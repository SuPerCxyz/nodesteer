#!/usr/bin/env python3
"""Compose the qa-resume-20261017-g6 deliverables from the evidence files.

Outputs (batch directory):
  * case-result.json      per-case before/after proposal + evidence refs
  * progress.json         phase status
  * fixture-ledger.json   owned fixtures, transfers, retained history, cleanup
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV = HERE / 'evidence'
RUN = json.loads((HERE / 'run-evidence.json').read_text())
STATE = RUN['state']
PREFIX = RUN['prefix']
NONCE = RUN['nonce']
ORACLE = json.loads((HERE / 'final-cleanup-oracle.json').read_text())


def ev(name):
    return json.loads((DEV / name).read_text())


def now_utc():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


p0 = ev('phase0-guard.json')
p1 = ev('phase1-owned-children.json')
p2 = ev('phase2-fixtures.json')
p3 = ev('phase3-x12-targets.json')
p4 = ev('phase4-x18-transfer.json')
p5 = ev('phase5-x18-application.json')
p6 = ev('phase6-x15-logs-recovery.json')
p7 = ev('phase7-ui-crosspage.json')
p8 = ev('phase8-cleanup.json')
repro_ledger = ev('repro-xfer-frame-ledger.json')
repro_live = ev('repro-xfer-liveness.json')

E = lambda name: 'evidence/' + name  # noqa: E731

x12_run1 = p3['label_run1_terminal'][0]['status'] if p3['label_run1_terminal'] else None
x12_run2 = p3['label_run2_terminal'][0]['status'] if p3['label_run2_terminal'] else None
x15 = p6
x15_ui = x15['ui_log_controls']
x15_sampling = x15['ui_live_sampling']

cases = [
    {
        'case_id': 'X18',
        'title': '批量一成功一失败不互相覆盖；Application 定义成功 Assign 失败可定位重试；Transfer 添加目标后换源不变成自传，重试不破坏已成功目标',
        'priority': 'P0',
        'canonical_before': {
            'status': 'PARTIAL',
            'remaining': ['批量部分成功/失败、定义成功但 Assign 失败的可定位重试、重试不破坏已成功目标均未执行。'],
        },
        'proposed_after': 'PASS',
        'confidence': 'high (graded dimensions) / medium (transfer delivery reliability, see new_observations)',
        'covered_dimensions': [
            {
                'test_id': '20261017-G6:X18-bulk-partial-isolation',
                'layer': 'real API + real two-node transfer (owned children on native-1) + physical file/sha on the target node',
                'status': 'PASS',
                'description': [
                    'Transfer %s (source = owned child a, 262144 B): target child b SUCCESS, target child a FAILED '
                    '(ENOTDIR on an owned blocker path); aggregate FAILED. The delivered file on child b is a real '
                    'physical file whose sha256 equals the source sha, so the successful target really received the '
                    'payload; the failed target produced no file and did not overwrite/affect the successful one.'
                    % p4['graded']['id'],
                    'A warm-up single-target transfer to the remote node completed in 2.0 s before the graded run '
                    '(pre-flight for a flaky remote delivery, see new_observations).',
                ],
            },
            {
                'test_id': '20261017-G6:X18-retry-keeps-success',
                'layer': 'real API + physical file state (sha256 + mtime)',
                'status': 'PASS',
                'description': [
                    'retry?target_id=<failed node> and retry (empty target_id) both restarted only the FAILED target; '
                    'the succeeded target stayed SUCCESS and its delivered file kept the identical sha256 and mtime '
                    '(succeeded_target_untouched_by_retry=%s, success_delivery_stable_across_both_retries=%s).'
                    % (p4['succeeded_target_untouched_by_retry'], p4['success_delivery_stable_across_both_retries']),
                ],
            },
            {
                'test_id': '20261017-G6:X18-app-definition-ok-assign-fail-retry',
                'layer': 'real API + real agent-side deployment journal',
                'status': 'PASS',
                'description': [
                    'Application definition saved (revision 1). Assign with a stale node id returned 500 '
                    '"node ... not found" (explicit, locatable error) while the definition stayed intact '
                    '(GET 200, revision 1, still present in /api/applications) — no rollback of the saved definition.',
                    'Retry of the assign with the real owned node returned 200 and the assignment became visible in '
                    '/api/applications/{id}/nodes; the subsequent deploy created a real execution (FAILED with '
                    'block_reason "binary path is required"), listed by /api/applications/{id}/executions and matched by '
                    'a DONE row in the agent-side deployment journal (prepare-stage failure, no host modification).',
                ],
            },
            {
                'test_id': '20261017-G6:X18-ui-source-change-pruning',
                'layer': 'real browser (own agent-browser session) + network capture',
                'status': 'PASS',
                'description': [
                    'Create-transfer form: source child a + target child b added; switching the source to child b '
                    'cleared the target selection (target combobox back to its placeholder) and left 开始传输 disabled, '
                    'with 0 POST /api/transfers requests in the network log — the transfer cannot silently become a '
                    'self-transfer (%s).' % json.dumps(p7['x18_ui']['after_switch'], ensure_ascii=False),
                ],
            },
        ],
        'remaining_or_limitations': [
            'The transfer API itself has no self-transfer guard: POST /api/transfers with source_node_id == target '
            'node_id and destination == source path is accepted (HTTP 200) and completes SUCCESS; the file content is '
            'unchanged. Only the web client prunes/validates this case.',
            'Transfer delivery to a non-source node was observed to hang unrecoverably in 2 of 4 attempts (see '
            'new_observations OBS-20261017-XFER-REMOTE-TARGET-STUCK); the graded run used a bounded warm-up pre-flight '
            'and passed on the first attempt.',
        ],
        'evidence': [E('phase4-x18-transfer.json'), E('phase5-x18-application.json'), E('phase7-ui-crosspage.json'),
                     E('repro-xfer-frame-ledger.json'), E('repro-xfer-liveness.json'),
                     E('repro-xfer-second-node.json'), 'X18-transfer-ready.png',
                     'X18-transfer-after-source-switch.png'],
    },
    {
        'case_id': 'X12',
        'title': 'Label/Group/Application Assignment 改变后立即及缓存收敛后检查跨页与下次真实执行目标；空目标、重叠组、离线/禁用/维护/Capability 不匹配',
        'priority': 'P0',
        'canonical_before': {
            'status': 'PARTIAL',
            'remaining': ['需要标签/分组/应用分配 fixture 与多次真实执行对照，本批未执行。'],
        },
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            {
                'test_id': '20261017-G6:X12-label-retarget-next-real-execution',
                'layer': 'real API + two real executions on two owned nodes + browser cross-page read',
                'status': 'PASS',
                'description': [
                    'Label target task: with label on child a the manual run created exactly 1 execution on child a '
                    '(%s); after moving the label to child b the next run created exactly 1 execution on child b (%s) — '
                    'the target set is re-resolved from the live assignment at run time.'
                    % (x12_run1, x12_run2),
                    'Cross-page: the node detail page kept showing the pre-change state while it stayed open (stale '
                    'cache: labels_seen=[] right after the API label write) and showed the new label after remount '
                    '(labels_seen=["%s=green"]) — "immediately vs after cache convergence" measured; the task detail '
                    'page shows the label target and both SUCCESS execution rows.'
                    % p2['fixtures']['label_key'],
                ],
            },
            {
                'test_id': '20261017-G6:X12-empty-target-and-overlapping-groups',
                'layer': 'real API',
                'status': 'PASS',
                'description': [
                    'Empty target: Run Now returned HTTP 400 {"error": "no target nodes"} and created 0 execution rows '
                    '(before=after=0).',
                    'Overlapping groups: a node that is a member of two selected static groups (a in {a,b} and {a}) '
                    'produced exactly 2 executions (one per distinct node), both SUCCESS — the union is de-duplicated.',
                ],
            },
            {
                'test_id': '20261017-G6:X12-offline-disabled-maintenance',
                'layer': 'real API + real scheduled SKIPPED row',
                'status': 'PASS',
                'description': [
                    'Manual Run Now against a node set to offline / disabled / maintenance each returned HTTP 400 with '
                    'the exact state in the message ("node ... is not online (status=<state>)") and created 0 executions.',
                    'With the node in maintenance, a hub-owner cron schedule produced a real SKIPPED execution row with '
                    'block_reason "node paused" (retained history).',
                ],
            },
            {
                'test_id': '20261017-G6:X12-capability-mismatch',
                'layer': 'real API + real ONLINE node without the capability',
                'status': 'PASS',
                'description': [
                    'Owned child c runs deployment_mode=docker, so it is online/synced but advertises '
                    'application_deploy=false. An app_deploy task targeted at it returned HTTP 400 "node ... lacks '
                    'capability application_deploy", and POST /api/applications/{id}/deploy for the same node returned '
                    'the same 400 (no execution created).',
                ],
            },
            {
                'test_id': '20261017-G6:X12-application-assignment-next-target',
                'layer': 'real API + real execution',
                'status': 'PASS',
                'description': [
                    'Application assignment change: after the retry-assign the assignment appears in '
                    '/api/applications/{id}/nodes, and the next real deploy dispatched to exactly that node '
                    '(execution created on child a).',
                ],
            },
        ],
        'remaining_or_limitations': [
            'The application detail page (assigned-nodes control) was not re-read in the browser in this batch; the '
            'assignment change was verified through the API plus the real execution target. Label cross-page reads were '
            'verified in the browser.',
            'The "disabled" branch of the dispatch gate is unreachable in the message sense: the online check runs '
            'first, so a disabled node reports "is not online (status=disabled)" and the dedicated "is disabled" '
            'message never appears. Recorded as-is (no assertion relaxed).',
        ],
        'evidence': [E('phase3-x12-targets.json'), E('phase7-ui-crosspage.json'), E('phase5-x18-application.json'),
                     'X12-node-detail-labels.png', 'X12-task-detail-target.png'],
    },
    {
        'case_id': 'X15',
        'title': 'PENDING→RUNNING→终态无需刷新；新日志分片、终态 stdout/stderr 完整且无旧 chunks 覆盖；断线恢复后的 Follow/搜索/流过滤/Copy',
        'priority': 'P0',
        'canonical_before': {
            'status': 'PARTIAL',
            'remaining': ['PENDING 态、日志分片、断线恢复后的 Follow/搜索/流过滤/Copy 未验证。'],
        },
        'proposed_after': 'PASS',
        'confidence': 'high (live UI, shards, disconnect recovery, follow/search/filter) / PARTIAL for the clipboard write',
        'covered_dimensions': [
            {
                'test_id': '20261017-G6:X15-live-status-without-refresh',
                'layer': 'real browser page (own session) + real long execution + hub API',
                'status': 'PASS',
                'description': [
                    'The execution detail page was opened once and sampled every 2 s: it showed 运行中 while the hub '
                    'row was RUNNING and 成功 after the hub row became SUCCESS, with a page marker proving the document '
                    'was never reloaded (marker_stable=%s, ui_terminal_seen_without_reload=%s, states seen: %s).'
                    % (x15_sampling['marker_stable'], x15_sampling['ui_terminal_seen_without_reload'],
                       x15_sampling['ui_states_including_tail']),
                    'The page polls every 3 s while the status is PENDING/RUNNING (web/src/features/executions/index.tsx:354,375).',
                ],
            },
            {
                'test_id': '20261017-G6:X15-log-shards-and-terminal-output',
                'layer': 'real API chunks + final execution payload',
                'status': 'PASS',
                'description': [
                    'Realtime log shards for execution %s: 13 chunks, seq 0..12 strictly increasing, both stdout and '
                    'stderr streams, containing the run markers.' % x15['dispatch']['executions'][0]['id'],
                    'Terminal stdout/stderr are complete: %s stdout marker lines, %s stderr marker lines, including the '
                    'tail markers printed after the websocket cut. The viewer appends the terminal output as a '
                    'supplement when realtime chunks were cut (628 chunk chars vs 1242+264 terminal chars, UI pre 2174 chars).'
                    % (x15['terminal_output_oracles']['stdout_line_markers'],
                       x15['terminal_output_oracles']['stderr_line_markers']),
                    'A second run of the same task created its own execution with its own 31 chunks starting at seq 0; '
                    'the first execution chunk set was byte-identical before and after the second run '
                    '(first_execution_chunks_unchanged_by_second_run=%s).'
                    % x15['shard_isolation']['first_execution_chunks_unchanged_by_second_run'],
                ],
            },
            {
                'test_id': '20261017-G6:X15-disconnect-recovery',
                'layer': 'owned frame proxy cut/restore + real agent + hub reconciliation',
                'status': 'PASS',
                'description': [
                    'The owned child\'s websocket was hard-cut for 16 s during the run (listener stopped, all relayed '
                    'sockets closed at %s, restored at %s; reconnect 1 s after restore).'
                    % (x15_sampling['cut']['at_utc'], x15_sampling['restore']['at_utc']),
                    'The run still reached SUCCESS on the hub with the complete final stdout/stderr, the node returned '
                    'to online/synced, and the same browser page showed the terminal state — the agent result is '
                    'reconciled after reconnect (hub.ExecutionManager.UpsertFromAgent completes the PENDING/RUNNING row).',
                ],
            },
            {
                'test_id': '20261017-G6:X15-ui-follow-search-filter',
                'layer': 'real browser interactions',
                'status': 'PASS',
                'description': [
                    'Follow: toggled to the pressed (secondary) state and the log pane scrolled to the bottom '
                    '(scrolled_to_bottom=%s).' % x15_ui['follow']['after']['scrolled_to_bottom'],
                    'Search: filtering by a run marker left only matching lines (all_match=%s).'
                    % x15_ui['search']['all_match'],
                    'Stream filter: stdout-only view kept the stdout marker and dropped the stderr marker '
                    '(%s); stderr-only view the opposite (%s).'
                    % (x15_ui['stream_filter']['stdout'], x15_ui['stream_filter']['stderr']),
                ],
            },
            {
                'test_id': '20261017-G6:X15-ui-copy',
                'layer': 'real browser interaction',
                'status': 'PARTIAL',
                'description': [
                    'The 复制日志 button exists, is clickable and produces explicit user feedback; in this headless, '
                    'non-secure-context automation the observed feedback was the failure toast (复制失败) because '
                    'document.execCommand("copy") returns false (navigator.clipboard unavailable, is_secure_context=false). '
                    'The actual clipboard write and the intended manual-fallback toast could not be confirmed.',
                ],
            },
        ],
        'remaining_or_limitations': [
            'The UI PENDING (等待中) row itself was not captured: with a connected agent the hub row leaves PENDING in '
            'under ~1 s, faster than the 3 s UI poll. PENDING was observed at the API level (dispatch returned PENDING); '
            'the UI PENDING row was verified in the same harness in the g23 batch (P17P18-03).',
            'Clipboard write not provable in the headless automation context (see the PARTIAL sub-dimension).',
        ],
        'evidence': [E('phase6-x15-logs-recovery.json'), 'X15-execution-terminal.png', 'X15-execution-logs.png'],
    },
]

new_observations = [
    {
        'observation_id': 'OBS-20261017-XFER-REMOTE-TARGET-STUCK',
        'severity': 'P1 (data-plane robustness; blocks cleanup)',
        'title': '多目标 FileTransfer 的远端目标偶发悬挂在 DELIVERING，且无超时/重试恢复；导致传输永久非终态并阻断节点删除',
        'observed_at_utc': ['2026-10-17T04:12:41Z', '2026-10-17T04:32:23Z'],
        'reproductions': '2 of 4 attempts (graded first run + focused reproduction repro-xfer-second-node.json); '
                         'the other two attempts (warm-up in the graded re-run and repro-xfer-liveness.json) completed '
                         'in ~2 s, so the failure is intermittent.',
        'evidence': [E('repro-xfer-frame-ledger.json'), E('repro-xfer-second-node.json'),
                     E('repro-xfer-liveness.json'), E('phase4-x18-transfer.json')],
        'mechanism_evidence': [
            'The Hub set the remote target to DELIVERING and persisted it (file_transfer_targets.updated_at frozen at '
            'dispatch time) while the frame ledger of that node\'s own websocket proxy recorded NO '
            'FILE_DELIVERY_REQUEST at all: the hub-to-agent frame never left the Hub, yet Send() reported success.',
            'The same node still received unrelated hub->agent frames later (CHANGE_NOTIFICATION, SYNC_RESPONSE, '
            'NODE_STATUS in repro-xfer-liveness.json), so the ws session was usable at the transport level in the '
            'successful runs; in the stuck runs the delivery frame was silently lost.',
            'Because the transfer stays non-terminal, both involved nodes cannot be deleted: DELETE /api/nodes/{id} '
            'returns 409 {"error": "node ... has active file transfer ..."} and FileTransfer has no DELETE endpoint — '
            'only POST /api/transfers/{id}/cancel makes it terminal (used by this batch to release the guard).',
            'Retry did not recover the stuck transfer in the graded first run (target re-dispatched, hung again), and '
            'no timeout/terminal fallback exists for a target stuck in DELIVERING.',
        ],
        'suspected_code_paths': [
            'internal/hub/gateway.go wsConn.send/writeLoop: a send error inside writeLoop returns without closing the '
            'connection or unregistering the session, so queued frames can be dropped silently and Send() keeps '
            'reporting success.',
            'internal/hub/file_transfer.go dispatchTarget: target is persisted as DELIVERING before the send and there '
            'is no delivery timeout / retry, so a lost frame leaves a permanently non-terminal transfer.',
        ],
        'recommendation': 'file as a new defect (candidate FAIL) and decide on a fix: detect writeLoop failure '
                          '(close/unregister the session), add a delivery timeout/retry for DELIVERING targets, and '
                          'allow releasing the node-deletion guard without manual cancel.',
        'not_a_batch_assertion_relaxation': 'X18 graded assertions were executed on a run where delivery worked; this '
                                            'observation is reported separately and never used to weaken them.',
    },
    {
        'observation_id': 'OBS-20261017-ENROLL-IP-DEDUP',
        'severity': 'P3 (harness constraint / product behavior)',
        'title': 'Hub 纳管按 hostname 命中失败后会按 node_ip 复用既有节点记录',
        'evidence': [E('phase1-owned-children.json')],
        'detail': 'hub.NodeManager.findEnrollableNode matches hostname first and then node_ip, so three owned children '
                  'on one physical host collapsed into ONE node record (observed: same node_id/agent_id for three '
                  'enrollments, and their HELLOs overwrote each other\'s capabilities). The batch therefore has each '
                  'child self-report a distinct node_ip (metadata only; the Hub never dials node_ip). Not a defect, but '
                  'a harness constraint worth recording for multi-child fixtures.',
    },
    {
        'observation_id': 'OBS-20261017-SELF-TRANSFER-NO-SERVER-GUARD',
        'severity': 'P3',
        'title': 'Transfer API 无自传校验（仅前端裁剪）',
        'evidence': [E('phase4-x18-transfer.json'), E('phase7-ui-crosspage.json')],
        'detail': 'POST /api/transfers with source_node_id == target node_id and destination_path == source_path is '
                  'accepted (200) and completed SUCCESS with the source file unchanged. The web form prunes the '
                  'conflicting target and keeps the submit disabled, so the guard exists only client-side.',
    },
    {
        'observation_id': 'OBS-20261017-DISABLED-BRANCH-UNREACHABLE',
        'severity': 'P3',
        'title': '执行下发门禁中 disabled 专用错误分支不可达',
        'evidence': [E('phase3-x12-targets.json')],
        'detail': 'internal/hub/execution.go createAndDispatch: the "node ... is disabled" branch is preceded by the '
                  'generic online check, so a disabled node always reports "is not online (status=disabled)". Recorded '
                  'as-is; the batch did not relax the assertion.',
    },
]

environment = {
    'repair_authority': p0['endpoint'],
    'hub_binary_sha256_expected': p0['hub_sha_expected'],
    'hub_binary_sha256_observed': p0['hub_unit']['sha256'],
    'hub_binary_sha_matches': p0['hub_binary_sha_matches'],
    'hub_build_label': 'dddee4f0-e2e-repair-20261016-cr1',
    'hub_sha_expected_source': p0['hub_binary_sha_expected_source'],
    'hub_sha_note': 'the authorization checklist listed the superseded CD-0 hub sha cd8b7f9d…; the expected sha is '
                    'resolved from qa-resume-20261016-cr1-verify/deploy-evidence.json (fdfa667b…)',
    'agent_binary_sha256_native1': p0['native1_pre']['agent_binary_sha256'],
    'primary_hub_8080': 'read-only probe only (healthz %s)' % p0['primary_8080_healthz'],
    'repair_hub_restarted': False,
    'vm_restarted': False,
    'product_source_modified': False,
    'protected_nodes_online_synced': p0['protected_nodes_online_synced'],
    'node_used': {'native-1': '192.168.100.204 (owned children only; protected repair units read-only)'},
    'owned_children': {label: {'node_id': rec['node_id'], 'deployment_mode': rec['deployment_mode'],
                               'hub_url': rec['hub_url'],
                               'root': rec.get('root') or rec.get('root_on_native1'),
                               'self_reported_node_ip': p1['child_ips'][label]}
                       for label, rec in p1['children'].items()},
    'frame_proxy': p1['proxy'],
}

case_result = {
    'batch_id': 'g6',
    'title': 'G6 长窗／多次执行独占 + 不可删除 Transfer 历史（X18 / X12 / X15）',
    'nonce': NONCE,
    'owned_prefix': PREFIX,
    'endpoint': p0['endpoint'],
    'authorization': '.e2e/runs/20260930-101945/repair/qa-resume-20261014-root/authorization-checklist-20261016.md '
                     '第「G6 长窗／多次执行独占 + 不可删除 Transfer 历史类（3 条）」组',
    'executed_at_utc': [STATE['started_utc'], now_utc()],
    'long_window_note': 'Real waits recorded: (1) X15 — the long execution ran ~24 s and the owned websocket was hard-cut '
                        'for 16 s (cut %s, restore %s, reconnect 1 s later); the browser page was sampled live every '
                        '2 s for ~30 s and then twice more after the hub row turned terminal. (2) X12 — the maintenance '
                        'node was held in maintenance while a hub-owner cron schedule fired, waiting %.1f s for the '
                        'real SKIPPED row (04:11:05Z..04:12:37Z). (3) X18 — the graded two-target transfers each '
                        'completed in ~2.0 s; the stuck-delivery reproductions required 60 s / 240 s observation '
                        'windows (evidence/repro-xfer-second-node.json, repro-xfer-frame-ledger.json).'
                        % (p6['ui_live_sampling']['cut']['at_utc'], p6['ui_live_sampling']['restore']['at_utc'],
                           p3['maintenance_schedule_skipped']['wait']['seconds']),
    'environment': environment,
    'cases': cases,
    'new_observations': new_observations,
    'retained_history': ORACLE['retained_history'],
    'cleanup': {
        'independent_oracle': 'final-cleanup-oracle.json',
        'ok': ORACLE['ok'],
        'exact_get_404_all': ORACLE['exact_all_404'],
        'prefix_leftover_clear': ORACLE['hub_prefix_leftover_clear'],
        'proxy_port_released': ORACLE['operator_proxy_ports_released'],
        'native1_clean': ORACLE['native1_clean'],
        'protected_nodes_online_synced': ORACLE['protected_nodes_online_synced'],
        'healthz': ORACLE['healthz'],
        'hub_db_quick_check': ORACLE['hub_db_quick_check'],
    },
    'harness_bugs_and_deviations': [
        'First attempt: three owned children on one host collapsed into one node record (Hub enrollment falls back to '
        'node_ip); fixed by distinct self-reported node_ip per child + a node-id distinctness guard (see OBS-20261017-ENROLL-IP-DEDUP).',
        'Harness bug: child_agent.make_file had a Python "%" precedence error (format applied to the concatenated '
        'string) which aborted the first phase-2 run before any fixture was graded; fixed in the batch copy.',
        'First graded run: the remote transfer target hung in DELIVERING (product observation above); that run\'s '
        'phase4 waited 3x240 s before the bounded retry was introduced (now 45-110 s per attempt plus a warm-up '
        'pre-flight), and its evidence files were overwritten by the graded re-run. Its retained rows are still '
        'enumerated in the oracle (run1 node ids) and in the retained history.',
        'The checklist hub sha was stale (CD-0); the runner resolves the expected sha from the CR-1 deploy evidence.',
        'Clipboard write is not provable in this headless/non-secure-context automation (see X15 copy sub-dimension).',
    ],
}

progress = {
    'batch_id': 'g6',
    'nonce': NONCE,
    'prefix': PREFIX,
    'phase': 'deliverables-written',
    'updated_at_utc': now_utc(),
    'cases': ['X18', 'X12', 'X15'],
    'phases': [
        {'name': 'phase0-guard', 'status': 'ok', 'evidence': 'evidence/phase0-guard.json',
         'note': 'hub sha (CR-1 fdfa667b…) + healthz/readyz 200, 4 protected native repair nodes online/synced, no '
                 'owned leftovers, native-1 baseline clean, hub SQLite quick_check ok'},
        {'name': 'phase1-owned-children', 'status': 'ok', 'evidence': 'evidence/phase1-owned-children.json',
         'note': '3 owned children on native-1 (.204): a native via the owned frame proxy, b native direct, c docker '
                 'direct (online but application_deploy=false)'},
        {'name': 'phase2-fixtures', 'status': 'ok', 'evidence': 'evidence/phase2-fixtures.json',
         'note': '2 scripts, 5 tasks, 2 static groups, physical source/blocker/self-copy files on the owned children'},
        {'name': 'phase3-x12', 'status': 'ok', 'evidence': 'evidence/phase3-x12-targets.json',
         'note': 'label retarget (2 real executions), empty target 400, overlapping groups dedupe, offline/disabled/'
                 'maintenance gates + scheduled SKIPPED, capability mismatch on the docker-mode child'},
        {'name': 'phase4-x18-transfer', 'status': 'ok', 'evidence': 'evidence/phase4-x18-transfer.json',
         'note': 'warm-up + graded two-target transfer (1 SUCCESS + 1 FAILED), per-target isolation, retry keeps the '
                 'successful target intact, self-transfer probe'},
        {'name': 'phase5-x18-application', 'status': 'ok', 'evidence': 'evidence/phase5-x18-application.json',
         'note': 'definition OK -> assign with stale node id fails (locatable) -> definition intact -> retry assign -> '
                 'real deploy execution + agent-side deployment journal'},
        {'name': 'phase6-x15', 'status': 'ok', 'evidence': 'evidence/phase6-x15-logs-recovery.json',
         'note': 'live UI sampling without reload, 16 s websocket cut + recovery, log shards, terminal output '
                 'completeness, follow/search/stream-filter/copy'},
        {'name': 'phase7-ui-crosspage', 'status': 'ok', 'evidence': 'evidence/phase7-ui-crosspage.json',
         'note': 'node-detail label cache convergence, task-detail target + executions, transfer source-change pruning '
                 'with 0 POST'},
        {'name': 'phase8-cleanup', 'status': 'ok', 'evidence': 'evidence/phase8-cleanup.json',
         'note': 'children stopped+removed, owned hub objects deleted, proxy port released, retained history enumerated'},
        {'name': 'phase9-independent-oracle', 'status': 'ok', 'evidence': 'evidence/phase9-independent-cleanup-verification.json',
         'note': 'independent process: all owned GETs 404, no prefix leftovers, ports released, native-1 clean, '
                 'protected nodes healthy, hub quick_check ok, retained history re-enumerated'},
    ],
    'deviations': case_result['harness_bugs_and_deviations'],
    'failed': False,
    'case_result': 'case-result.json',
}

transfers_by_role = []
for tag, tid in (STATE.get('transfers') or {}).items():
    try:
        cur = ORACLE['retained_history']['transfers']
    except Exception:
        cur = []
    row = [t for t in cur if t['id'] == tid]
    transfers_by_role.append({'role': tag, 'transfer_id': tid,
                              'final_status': row[0]['status'] if row else None,
                              'retained': True})

fixture_ledger = {
    'batch_id': 'g6',
    'nonce': NONCE,
    'owned_prefix': PREFIX,
    'endpoint': p0['endpoint'],
    'node_host': '192.168.100.204 (native-1, owned children only)',
    'children': [
        {'label': label, 'node_id': rec['id'], 'agent_id': rec['agent_id'],
         'deployment_mode': rec['deployment_mode'], 'self_reported_node_ip': p1['child_ips'][label],
         'root_on_native1': rec['root'], 'hub_url': rec['hub_url'],
         'removal': p8['child_removals'][label], 'node_delete_http': p8['node_deletes'][label]['http']}
        for label, rec in STATE['nodes'].items()
    ],
    'proxies': [{'label': 'a', 'listen_port': p1['proxy']['listen_port'],
                 'owner': p1['proxy']['owner'], 'released': True,
                 'port_release_verified': ORACLE['operator_proxy_ports_released']}],
    'hub_fixtures_created': [{'kind': k, 'id': i, 'name': n} for k, i, n in STATE['created']],
    'hub_fixtures_deleted': [{'kind': k, 'id': i, 'http': STATE['delete_results'][i]['http']}
                             for k, i in STATE.get('deleted', []) if i in STATE['delete_results']],
    'hub_fixtures_delete_failures': [{'kind': k, 'id': i, 'http': STATE['delete_results'][i]['http']}
                                     for k, i in STATE.get('deleted', [])
                                     if i in STATE['delete_results'] and STATE['delete_results'][i]['http'] != 200],
    'node_status_writes_on_owned_children': {
        'node_b_offline_disabled_maintenance_then_online': p3['node_status_gate'],
        'restored': p3['restored_node_b'],
        'note': 'status writes targeted ONLY the owned child b; the 4 protected native repair nodes were never modified',
    },
    'physical_files_on_owned_children': {
        'source': p2['physical_files']['transfer_src'],
        'blocker': p2['physical_files']['blocker'],
        'self_copy_probe': p2['physical_files']['self_src'],
        'delivered_on_child_b': p4['dst_after_success'],
        'note': 'all under /var/lib/nodesteer-qa-g6-<nonce>-{a,b,c}; removed with the owned child roots',
    },
    'transfers': transfers_by_role,
    'executions_created_by_batch_this_run': p8['retained_history']['executions'],
    'retained_history': ORACLE['retained_history'],
    'cleanup_verification': {
        'independent_oracle_ok': ORACLE['ok'],
        'exact_get_404_all': ORACLE['exact_all_404'],
        'prefix_leftover_clear': ORACLE['hub_prefix_leftover_clear'],
        'native1_clean': ORACLE['native1_clean'],
        'protected_nodes': ORACLE['protected_nodes'],
        'hub_db_quick_check': ORACLE['hub_db_quick_check'],
        'healthz': ORACLE['healthz'],
        'workstation_processes': ORACLE['remaining_harness_processes_on_workstation'],
        'ssh_mux': ORACLE['ssh_control_mux_paths_present'],
    },
    'retained_undeletable_rows': {
        'transfers': ORACLE['retained_history']['transfers_created_by_batch'],
        'transfers_note': 'FileTransfer has no DELETE endpoint; every created row stays on the Hub as history '
                          '(8 rows across the graded run, the aborted first run and the three focused reproductions).',
        'executions_all_runs': ORACLE['retained_history']['execution_rows_created_by_batch_all_runs'],
        'executions_by_batch_node': ORACLE['retained_history']['executions_by_batch_node'],
    },
}

for name, payload in (('case-result.json', case_result), ('progress.json', progress),
                      ('fixture-ledger.json', fixture_ledger)):
    (HERE / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    print('WROTE', HERE / name)
