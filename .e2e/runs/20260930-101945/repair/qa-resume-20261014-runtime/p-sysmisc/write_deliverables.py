#!/usr/bin/env python3
"""Assemble the p-sysmisc deliverables from the recorded evidence.

Writes case-result.json, refreshes fixture-ledger.json (adding the UI sub-run) and
progress.json. Reads only files produced by this batch.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
E = HERE / 'evidence'


def j(name):
    return json.loads((E / name).read_text())


def main():
    run = json.loads((HERE / 'run-evidence.json').read_text())
    state = run['state']
    orc = json.loads((HERE / 'final-cleanup-oracle.json').read_text())
    led = json.loads((HERE / 'fixture-ledger.json').read_text())
    p1 = j('phase1-enrollment-pending-a.json')
    p2 = j('phase2-hello-online-a.json')
    p3 = j('phase3-fixtures.json')
    p4 = j('phase4-heartbeat-window-a.json')
    p5 = j('phase5-local-state-and-sockets-a.json')
    p6 = j('phase6-restart-persistence-a.json')
    p7 = j('phase7-sys01-tokens-b.json')
    p8 = j('phase8-inv05-commit-failure-c.json')
    p9 = j('phase9-e2e18-adopt-attempt02.json')
    p10 = j('phase10-ui-feedback-u.json')

    nodes = state.get('nodes', {})
    fixtures = state.get('fixtures', {})
    scheds = state.get('schedules', {})
    ui_node_id = (p10.get('cleanup', {}).get('node_delete', {}) or {}).get('id')

    base = '.e2e/runs/20260930-101945/repair/qa-resume-20261014-runtime/p-sysmisc/'
    ev = lambda *names: [base + 'evidence/' + n for n in names]

    cleanup_summary = {
        'all_get_404': orc['exact_all_404'],
        'hub_prefix_leftover_clear': orc['hub_prefix_leftover_clear'],
        'native1_clean': orc['native1_clean'],
        'native1_roots_removed': {k: (not v['exists']) for k, v in orc['native1']['roots'].items()},
        'native1_owned_processes_after': orc['native1']['owned_processes'],
        'native1_tmpfs_mounts_after': orc['native1']['psm_mounts'],
        'operator_proxy_ports_released': orc['operator_proxy_ports_released'],
        'protected_nodes_online_synced': orc['protected_nodes_online_synced'],
        'healthz': orc['healthz'],
        'hub_db_quick_check': orc['hub_db_quick_check'],
        'deleted_fixtures': [{'kind': d['kind'], 'id': d['id'], 'get_http': d['get_http']}
                             for d in json.loads((E / 'phase-cleanup.json').read_text())['deleted']],
        'ui_subrun_node': p10.get('cleanup', {}).get('node_delete'),
        'defensive_sweep': run.get('defensive_sweep'),
        'independent_verifier': 'evidence/phase12-independent-cleanup-verification.json',
    }

    cases = [
        {
            'case_id': 'E2E-16',
            'title': 'Native Enrollment → pending → HELLO → online/synced',
            'canonical_before': 'PARTIAL (pending phase unobserved, HELLO/heartbeat wire evidence missing, UI feedback unverified)',
            'proposed_after': 'PASS',
            'confidence': 'high',
            'covered_dimensions': [{
                'test_id': '20261014-PSYSMISC:E2E-16-pending-hello-ui',
                'layer': 'real-api + frame-wire + real SPA',
                'status': 'PASS',
                'description': [
                    'Enrollment created the node pending (Hub REST status=pending, sync_status=pending) before any Agent contact; after the owned child Agent HELLO the node moved to online/synced (state transition observed with timestamps).',
                    'Frame proxy recorded the ordered wire sequence HELLO -> HELLO_ACK(accepted=true) -> SYNC_REQUEST -> INVENTORY -> SYNC_RESPONSE -> SYNC_ACK -> HEARTBEAT -> HEARTBEAT_ACK -> REVISION_CHECK -> REVISION_CHECK_ACK.',
                    'Repair Web UI showed the fresh node as 等待中 while pending and 在线 after HELLO (screenshots), matching the API state.',
                    'Reverse path (invalid registration token) leaves the node pending and is covered under SYS-01.',
                ],
            }],
            'remaining_or_limitations': [],
            'evidence': ev('phase1-enrollment-pending-a.json', 'phase2-hello-online-a.json',
                           'phase10-ui-feedback-u.json', 'ui-node-pending.png', 'ui-node-online.png'),
            'actual_resources': {
                'node_a': nodes.get('a', {}).get('id'), 'agent_a': nodes.get('a', {}).get('agent_id'),
                'ui_node': ui_node_id, 'child_root': '/var/lib/nodesteer-qa-psm-a8fd4885-a',
            },
            'frame_counts_pending_window': p4['counts_by_type'],
            'final_cleanup': cleanup_summary,
        },
        {
            'case_id': 'SYS-01',
            'title': 'Agent 首次注册：Registration Token、Stable Agent ID、Credential、Node 记录',
            'canonical_before': 'PARTIAL (no independent invalid/expired/revoked token or re-enrollment duplicate test)',
            'proposed_after': 'PASS',
            'confidence': 'high',
            'covered_dimensions': [{
                'test_id': '20261014-PSYSMISC:SYS-01-token-credential-reenroll',
                'layer': 'real-api + frame-wire + Hub DB',
                'status': 'PASS',
                'description': [
                    'Invalid registration token: HELLO_ACK accepted=false message="invalid registration token"; the node stayed pending (never online).',
                    'Re-enrollment with the same node_name/node_ip reuses the exact Node ID and Agent ID and creates no duplicate node row (owned node count stayed 2).',
                    'Valid registration token: HELLO_ACK accepted=true; node online/synced.',
                    'Revoked credential: POST /api/nodes/{id}/revoke-credential -> node status disabled; on restart the Agent presented the now-revoked credential and got HELLO_ACK accepted=false "invalid credential"; it then fell back to the registration token which the Hub also refused while revoked, so it could not re-register.',
                    'Re-enrollment clears the revoked flag; the Agent reconnected with the registration token, was accepted (HELLO_ACK accepted=true) and returned to online/synced with the same Node ID / Agent ID.',
                ],
            }],
            'remaining_or_limitations': [
                'No product-level token expiry exists: the registration token and the per-node credential are static server-side values with no TTL (internal/hub/gateway.go:351, internal/hub/node.go:154-161), so an "expired token" case is N/A rather than untested.',
                'The credential value is intentionally never exported; the evidence proves accept/reject plus local secure storage (0600 config, identity row present), not raw value equality.',
            ],
            'evidence': ev('phase7-sys01-tokens-b.json', 'phase0-guard.json'),
            'actual_resources': {
                'node_b': nodes.get('b', {}).get('id'), 'agent_b': nodes.get('b', {}).get('agent_id'),
                'child_root': '/var/lib/nodesteer-qa-psm-a8fd4885-b',
            },
            'final_cleanup': cleanup_summary,
        },
        {
            'case_id': 'SYS-02',
            'title': '已注册 Agent 重启：HELLO、Heartbeat、online/synced、ID 不变',
            'canonical_before': 'PARTIAL (no explicit HELLO/Heartbeat message or count evidence)',
            'proposed_after': 'PASS',
            'confidence': 'high',
            'covered_dimensions': [{
                'test_id': '20261014-PSYSMISC:SYS-02-hello-heartbeat-restart',
                'layer': 'real-api + frame-wire + owned child restart',
                'status': 'PASS',
                'description': [
                    'Same binary/config child Agent was SIGTERM-stopped and restarted; it produced a second HELLO and returned to online/synced with the identical Node ID and Agent ID.',
                    'Frame proxy counted actual control messages (not last_seen): HELLO=1, HELLO_ACK(accepted)=1, HEARTBEAT=3, HEARTBEAT_ACK=3, REVISION_CHECK=2, REVISION_CHECK_ACK=2 over a 72 s window, plus a second HELLO after the restart.',
                    'Hub heartbeat/revision handlers drive the observed HEARTBEAT_ACK / REVISION_CHECK_ACK replies (internal/hub/gateway.go:426-478).',
                ],
            }],
            'remaining_or_limitations': [
                'Only the native deployment form is exercised; Docker / docker_host_integration restart variants are not (out of scope for this batch).',
            ],
            'evidence': ev('phase2-hello-online-a.json', 'phase4-heartbeat-window-a.json',
                           'phase6-restart-persistence-a.json'),
            'actual_resources': {'node_a': nodes.get('a', {}).get('id')},
            'final_cleanup': cleanup_summary,
        },
        {
            'case_id': 'SYS-11',
            'title': 'Native Agent 本地 SQLite/WAL、Identity、Revision、定义持久化',
            'canonical_before': 'PARTIAL (local WAL/Identity/Revision/definition persistence and restart consistency not proven)',
            'proposed_after': 'PASS',
            'confidence': 'high',
            'covered_dimensions': [{
                'test_id': '20261014-PSYSMISC:SYS-11-local-persistence-restart',
                'layer': 'real owned child Agent SQLite + restart',
                'status': 'PASS',
                'description': [
                    'Local store is real SQLite in WAL mode (PRAGMA journal_mode=wal, state.db-wal/-shm present, quick_check=ok).',
                    'Identity rows (node_id, agent_id, credential) are present; script/task/schedule definitions synced from the Hub are stored locally with their revisions.',
                    'After a full child restart the same identity, the persisted global_revision (not reset) and the same script/task/schedule rows (id, revision, sha256) were read back, with a second HELLO and online/synced.',
                ],
            }],
            'remaining_or_limitations': [
                'The sync-transaction failure path of the same store is defective and is reported under INV-05; SYS-11 persistence itself is intact.',
            ],
            'evidence': ev('phase5-local-state-and-sockets-a.json', 'phase6-restart-persistence-a.json',
                           'phase3-fixtures.json'),
            'actual_resources': {'node_a': nodes.get('a', {}).get('id')},
            'final_cleanup': cleanup_summary,
        },
        {
            'case_id': 'INV-02',
            'title': 'Agent 主动连接 Hub',
            'canonical_before': 'PARTIAL (no /proc socket or listener inventory for a QA-owned child)',
            'proposed_after': 'PASS',
            'confidence': 'high',
            'covered_dimensions': [{
                'test_id': '20261014-PSYSMISC:INV-02-proc-sockets',
                'layer': 'node /proc ground truth on the owned child',
                'status': 'PASS',
                'description': [
                    'Sampled /proc/<pid>/fd socket inodes of the owned child Agent and mapped them to /proc/net/tcp: exactly one ESTABLISHED outbound socket to the operator proxy port (0x9301 = 37633).',
                    'The child owned zero LISTEN sockets, so there is no reverse listener; the Agent only dials out.',
                    'ss -tanp filtered by the child pid confirmed the same single outbound connection.',
                ],
            }],
            'remaining_or_limitations': [
                'Captured for the native owned child only; Docker/host topology socket checks remain owned by their own cases.',
            ],
            'evidence': ev('phase5-local-state-and-sockets-a.json'),
            'actual_resources': {'node_a': nodes.get('a', {}).get('id')},
            'final_cleanup': cleanup_summary,
        },
        {
            'case_id': 'INV-05',
            'title': 'Agent Revision 成功落盘后才能前进（CommitSync 失败路径）',
            'canonical_before': 'PARTIAL (CommitSync own failure path not tested)',
            'proposed_after': 'PARTIAL',
            'confidence': 'high',
            'covered_dimensions': [{
                'test_id': '20261014-PSYSMISC:INV-05-commitsync-failure',
                'layer': 'real owned child Agent + injected non-BUSY storage failure',
                'status': 'PARTIAL',
                'description': [
                    'Injected a real non-BUSY failure by running the owned child data dir on a size-limited tmpfs and delivering an oversized script, so the sync write transaction hit SQLITE_FULL.',
                    'Observed: the sync transaction did NOT persist (the oversized script never appeared locally), and the failing commit took the CommitSync error branch (internal/agent/agent.go:917-919).',
                    'DEFECT: on that CommitSync error the Agent fatally panics with "fatal error: sync: unlock of unlocked mutex" (internal/agent/store.go:258 via the deferred RollbackSync at internal/agent/agent.go:800), because CommitSync already unlocked and cleared tx and RollbackSync unlocks again. The Agent process died (pid gone) instead of rolling back and reporting a failed ACK.',
                    'Recovery: after freeing space and restarting the owned child, sync succeeded, the previously failing script was persisted and the revision advanced — local state was not left half-applied.',
                ],
            }],
            'remaining_or_limitations': [
                'The invariant "revision only advances after successful persistence" holds for the failing transaction (nothing persisted), but the required graceful failure semantics do NOT hold: any CommitSync error crashes the Agent. Reported as defect psm-INV05-commit-failure-double-unlock.',
            ],
            'evidence': ev('phase8-inv05-commit-failure-c.json'),
            'defect': p8.get('defect'),
            'actual_resources': {'node_c': nodes.get('c', {}).get('id'), 'child_root': '/var/lib/nodesteer-qa-psm-a8fd4885-c'},
            'final_cleanup': cleanup_summary,
        },
        {
            'case_id': 'E2E-18',
            'title': 'Docker Recreate → Identity/Revision/Journal/Cache 不丢',
            'canonical_before': 'PARTIAL (Journal dimension not covered in the cited evidence; attempt02 not referenced)',
            'proposed_after': 'PARTIAL',
            'confidence': 'medium',
            'covered_dimensions': [{
                'test_id': '20261014-PSYSMISC:E2E-18-journal-adoption',
                'layer': 'evidence reuse (same-case SYS-12 attempt02) reviewed',
                'status': 'PASS_FOR_REVIEWED_DIMENSIONS',
                'description': [
                    'Journal semantics: the same-case SYS-12 attempt02 summary records local SQLite execution-journal rows surviving a container recreate (execution_journal_status=SUCCESS, exit_code=0, script_revision=1, rows_unchanged_across_a_b=true) together with same node/agent identity and monotonic revisions; explicitly adopted here.',
                    'Identity/Revision/Cache remain as previously reviewed from attempt04.',
                ],
            }],
            'remaining_or_limitations': [
                'Docker/Compose recreate cannot be independently reproduced in this batch: it needs an exclusive batch and a docker-host deployment form, and the repair Hub must not be restarted.',
                'Compose-form recreate and the post-restart online/synced UI readback remain uncovered, so the case stays PARTIAL.',
            ],
            'evidence': ev('phase9-e2e18-adopt-attempt02.json'),
            'adopted_source_files': [p9.get('attempt02_journal', {}).get('file'),
                                     p9.get('attempt04_identity_revision_cache', {}).get('file')],
            'final_cleanup': cleanup_summary,
        },
    ]

    result = {
        'batch_id': 'p-sysmisc',
        'title': 'system 零散 PARTIAL 收口：enrollment/HELLO/heartbeat/persistence/sockets/commit-failure/UI',
        'nonce': run['nonce'],
        'owned_prefix': run['prefix'],
        'endpoint': 'http://192.168.100.209:18080 (repair authority; primary :8080 not written)',
        'node_used': {'native-1': '192.168.100.204'},
        'executed_at_utc': state.get('started_utc'),
        'finished_at_utc': run.get('finished_utc'),
        'cases': cases,
        'actual_budget': run['budget'],
        'final_cleanup': cleanup_summary,
        'deviations': [
            'Harness-level (not product) defects from earlier attempts were fixed before the final run: a format-string bug in the owned-child removal helper (left residue in two aborted attempts, cleaned up manually) and a %-escaping bug in the socket-inventory probe.',
        ],
        'harness_errors': [],
        'defects_added': [p8.get('defect')] if p8.get('defect') else [],
        'unverified_items': [
            'Docker/Compose recreate form (E2E-18) and Docker/host restart variants (SYS-02) — out of scope for this batch.',
        ],
    }
    (HERE / 'case-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')

    # append the UI sub-run resources to the fixture ledger
    if ui_node_id and not any(n.get('id') == ui_node_id for n in led.get('child_nodes', [])):
        ui_node_ip = p10['hostname'].replace('child-u', 'u') + '.invalid'
        ui_del = p10.get('cleanup', {}).get('node_delete', {}) or {}
        led['child_nodes'].append({
            'label': 'u', 'id': ui_node_id, 'agent_id': None,
            'hostname': p10['hostname'], 'node_ip': ui_node_ip, 'host': '192.168.100.204',
            'delete_http': ui_del.get('delete_http'), 'get_http': ui_del.get('get_http'),
            'deleted_get404': ui_del.get('get_http') == 404,
            'note': 'E2E-16 UI feedback sub-run',
        })
        led.setdefault('host_fixtures', []).append({
            'kind': 'owned_child_agent_process', 'host': '192.168.100.204', 'label': 'u',
            'root': '/var/lib/nodesteer-qa-psm-%s-u' % run['nonce'], 'tmpfs_mb': 0,
            'note': 'E2E-16 UI feedback sub-run; removed in phase10 cleanup',
        })
        led.setdefault('proxies', []).append({'label': 'u', 'listen_port': None, 'deployment_mode': 'native',
                                              'note': 'E2E-16 UI sub-run proxy (stopped in cleanup)'})
    led['status'] = 'CLEANED'
    (HERE / 'fixture-ledger.json').write_text(json.dumps(led, ensure_ascii=False, indent=2) + '\n')

    prog = json.loads((HERE / 'progress.json').read_text())
    prog['phase'] = 'done'
    prog['cases_proposed'] = {c['case_id']: c['proposed_after'] for c in cases}
    prog['verified_pending_or_cleanup'] = True
    (HERE / 'progress.json').write_text(json.dumps(prog, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'wrote': ['case-result.json', 'fixture-ledger.json', 'progress.json'],
                      'cases': {c['case_id']: c['proposed_after'] for c in cases}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
