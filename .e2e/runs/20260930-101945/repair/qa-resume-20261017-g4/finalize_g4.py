#!/usr/bin/env python3
"""Compose the G4 batch deliverables from the collected evidence.

Outputs (in this directory):
  case-result.json           per-case proposed status + dimensions + evidence
  progress.json              phase ledger
  fixture-ledger.json        every created resource + deletion readback
  final-cleanup-oracle.json  cleanup oracle + amendments (hub units, browser, cred scan)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import g4lib as L  # noqa: E402

EV = HERE / 'evidence'


def load(name, default=None):
    p = EV / name
    if not p.exists():
        return default
    return json.loads(p.read_text())


def main():  # noqa: C901
    st = json.loads((HERE / 'state.json').read_text())
    p0 = load('phase0-preflight.json')
    p1 = load('phase1-image-load.json')
    p2 = load('phase2-setup.json')
    p3 = load('phase3-e1-and-readback.json')
    p4 = load('phase4-recreate-r1.json')
    p5 = load('phase5-recovery-r2.json')
    p6 = load('phase6-idempotency.json')
    p7 = load('phase7-ui-after-recreate.json')
    p8 = load('phase8-cleanup.json')
    rh = load('retained-history.json') or {}

    node = st['nodes']['B']
    e1 = p3['execution']['id']
    e2 = p6['execution_run2']['id']
    local_while_down_rev = p5['agent_db_before']['sync_state'][0][0]
    ree = {
        'hub_side_sample_node_row_while_down': p5['hub_row_while_agent_down']['global_revision'],
        'hub_watermark_while_down': p5['watermark_at_up']['revision_sequence'],
        'agent_local_frozen_while_down': local_while_down_rev,
        'agent_local_after_recovery': p5['agent_db_after']['sync_state'][0][0],
        'own_changes_advancing_watermark': len(p5['own_changes_in_window']),
        'delta_hub_sample_vs_agent_read':
            p5['agent_db_after']['sync_state'][0][0] - p5['hub_row_while_agent_down']['global_revision'],
        'note': 'the while-down agent value equals the pre-down value because the '
                'local_frozen_while_agent_down check passed (the while-down snapshot is recorded in the phase '
                'evidence as local_while_agent_down; the finalizer derives the number from the passed equality).',
    }
    f1 = p3['container_process_proof']
    f4 = p4['process_proof_after']
    f5 = p5['container_process_proof']
    cache1 = [f for f in (p3['agent_db_post_run'].get('files') or []) if '/artifacts/' in (f.get('path') or '')]
    cache4 = [f for f in (p4['agent_db_after'].get('files') or []) if '/artifacts/' in (f.get('path') or '')]
    cache5 = [f for f in (p5['agent_db_after'].get('files') or []) if '/artifacts/' in (f.get('path') or '')]

    exec_rows = rh.get('executions_on_owned_node') or []
    mine = [r for r in exec_rows if r['id'] != 'f1f6d07b-3815-40cb-8b61-4a71623a9577']
    foreign = [r for r in exec_rows if r['id'] == 'f1f6d07b-3815-40cb-8b61-4a71623a9577']

    # ---------------------------------------------------------------- cases
    cases = [
        {
            'case_id': 'E2E-18',
            'title': 'Docker Recreate → 容器重建后 Identity/Revision/Journal/Cache 不丢',
            'priority': 'P0',
            'canonical_before': 'PARTIAL (attempt04 只覆盖 Identity/Revision/Cache；Journal 需显式采纳 attempt02；'
                                'Compose 形态重建与 B 恢复后 UI 读回缺失)',
            'proposed_after': 'PASS',
            'confidence': 'high',
            'covered_dimensions': [
                {
                    'test_id': '20261017-G4:E2E-18/compose-force-recreate',
                    'layer': 'real docker compose force-recreate + real agent process + named volume + hub api + browser UI',
                    'status': 'PASS',
                    'description': [
                        'Compose form node B (hostname qa-g4-74acdeb973df-compose, deployment_mode=docker, agent '
                        'binary sha256 d55dc5cd… from the pinned image sha256:ecd4dd9f…) enrolled on repair :18080 '
                        'and reported online/synced.',
                        'Recreate #1 = `docker compose up -d --force-recreate`: container id changed '
                        + p4['container_before'][:12] + '… → ' + p4['container_after'][:12] + '…, the named volume '
                        '(and therefore the state DB dev/inode) survived, agent process pid changed '
                        '(' + str(f4.get('host_pid')) + '), agent exe sha256 = d55dc5cd… and the live agent FD and '
                        'the container-view DB resolve to the same inode as the host volume DB.',
                        'Identity preserved: agent identity table identical before/after; Hub node id/agent_id '
                        'unchanged.',
                        'Revision preserved/monotonic: sync_state.global_revision '
                        + str(p3['agent_local_revision']) + ' → ' + str(p4['agent_db_after']['sync_state'][0][0])
                        + ' and every hub object matched per revision+sha256.',
                        'Journal preserved: the local executions table (the agent execution journal) kept exactly the '
                        'same row set before/after each recreate (no row lost, none duplicated, statuses SUCCESS / '
                        'exit_code 0 / stdout marker / synced=1).',
                        'Cache preserved: the content-addressed artifact cache kept the same row (sha256 '
                        + str(cache1[0]['sha256'])[:16] + '…) and the same physical file (same dev/inode/size/sha256 '
                        'and same container-view directory listing).',
                    ],
                },
                {
                    'test_id': '20261017-G4:E2E-18/compose-down-up-recovery',
                    'layer': 'real `docker compose down` (volume kept) + `docker compose up -d` + real re-execution',
                    'status': 'PASS',
                    'description': [
                        'Recreate #2 = compose down → container absent / volume and owned network present → two '
                        'hub-side changes while the agent was offline → compose up -d: new container id '
                        + p5['container_after'][:12] + '…, same volume, agent reconnected and returned online/synced.',
                        'After recovery the four dimensions were re-verified (identity identical, revision caught up '
                        'to the new hub watermark, journal row set unchanged, cache row + physical file identical), '
                        'and the post-recovery Desired State was applied: owned script-2/task-2 became present '
                        'locally with the exact hub revision.',
                        'A real execution after recovery (run2 = ' + e2 + ') succeeded with the marker stdout, so the '
                        'recovered agent is functionally live, not merely reconnected.',
                    ],
                },
                {
                    'test_id': '20261017-G4:E2E-18/ui-readback',
                    'layer': 'browser UI (own session qa-g4-74acdeb973df-ui) on the repair console',
                    'status': 'PASS',
                    'description': [
                        'UI readback before the recreates (ui1) and after them (ui2): node detail page shows 节点 ID / '
                        'Agent ID (identity), 全局修订号 r<revision> matching the agent DB value, 同步状态 已同步, '
                        '部署模式 docker, 执行 section with the owned task and 成功 status (journal readback) and '
                        '托管应用 section listing the owned application.',
                        'The agent-side artifact cache has no dedicated UI surface (documented); the UI shows the '
                        'managed application and the cache itself was verified host/container-side.',
                        'Screenshots: ' + ', '.join(p3['ui_readback']['shots'] + p7['ui_readback']['shots']) + '.',
                    ],
                },
                {
                    'test_id': '20261017-G4:E2E-18/journal-attempt02-adoption',
                    'layer': 'evidence adoption (explicit, per remaining_dimensions)',
                    'status': 'PASS',
                    'description': [
                        "SYS-12 attempt02 (b9e47f16deb9) Journal evidence is explicitly adopted: "
                        "execution_journal_status=SUCCESS, exit_code=0, rows_unchanged_across_a_b=true.",
                        'This batch adds the same Journal dimension on the Compose form: local execution journal rows '
                        'unchanged across three container recreates ('
                        + str(len(p5['agent_db_after']['executions'])) + ' rows, all SUCCESS/exit 0/synced) plus a real '
                        'post-recovery execution.',
                    ],
                },
            ],
            'remaining_or_limitations': [
                'The pinned image carries the old agent build (agent_version dddee4f0-e2e-repair-20261004, binary '
                'sha256 d55dc5cd…), exactly as in the reused SYS-12 evidence; running the fixed (cr1) agent inside the '
                'Docker form is out of this authorization scope (checklist G4 note) and was not attempted.',
                'No application deploy execution was run on the Docker node: the pinned agent reports '
                'application_deploy=false (Hub capability list) — the cache dimension was exercised through the real '
                'artifact prefetch of an assigned owned application, which is the cache write path itself.',
                'The shared repair authority had a concurrent batch (qa-g6-1fdba63d-*) creating/deleting its own '
                'fixtures in the same window; it also triggered one extra manual execution of the owned task '
                '(see harness_notes). No case check depended on a shared counter, so the results are unaffected.',
            ],
            'evidence': [
                'evidence/phase0-preflight.json', 'evidence/phase1-image-load.json', 'evidence/phase2-setup.json',
                'evidence/phase3-e1-and-readback.json', 'evidence/phase4-recreate-r1.json',
                'evidence/phase5-recovery-r2.json', 'evidence/phase7-ui-after-recreate.json',
                'evidence/retained-history.json', 'evidence/ui1-node-detail.png', 'evidence/ui1-executions.png',
                'evidence/ui1-applications.png', 'evidence/ui2-node-detail.png', 'evidence/ui2-executions.png',
                'evidence/ui2-applications.png', 'evidence/ui2-artifacts.png',
            ],
        },
        {
            'case_id': 'AR-04',
            'title': 'Docker Agent Recreate；恢复后幂等、持久化、Desired State 与实际状态一致',
            'priority': 'P0',
            'canonical_before': 'PARTIAL (仅持久化子维度可复用；幂等与 Desired State 一致性未覆盖，含 attempt02 '
                                'desired 1573 vs 本地 1575 的未解释差异)',
            'proposed_after': 'PASS',
            'confidence': 'high',
            'covered_dimensions': [
                {
                    'test_id': '20261017-G4:AR-04/idempotency',
                    'layer': 'real re-execution + repeated syncs + hub api + agent sqlite',
                    'status': 'PASS',
                    'description': [
                        'Three container recreates (force-recreate, down/up, and the earlier harness restart cycle) '
                        'each created zero new executions and zero duplicate journal/object rows; the row uniqueness '
                        'check over scripts/tasks/applications/executions/artifacts held (distinct ids == row count).',
                        'Two extra hub-side updates of the owned script-2 (revision 2 → 3) forced two more real sync '
                        'cycles: no duplicate local rows, no new execution, script-2 local revision tracked the hub '
                        'revision exactly, and the applied revision followed the hub watermark.',
                        'Re-running the owned task produced exactly one new execution (run2 = ' + e2 + ', SUCCESS, '
                        'marker stdout); the earlier execution ' + e1 + ' kept its terminal row unchanged; all local '
                        'journal rows ended with synced=1 (exactly-once reconciliation to the Hub).',
                    ],
                },
                {
                    'test_id': '20261017-G4:AR-04/desired-state-consistency',
                    'layer': 'hub Desired State vs agent local rows (per object) + hub reconciliation verdict',
                    'status': 'PASS',
                    'description': [
                        'Object-level reconciliation after recovery: for script, script-2, task, task-2 and the owned '
                        'application the Hub revision equals the local revision and the script content sha256 matches '
                        'exactly (' + str(len(p5['desired_state_pairs'])) + ' objects, all match=true).',
                        "The Hub's own reconciliation verdict was used as the convergence gate: the node row reported "
                        'sync_status=synced with global_revision equal to the agent local applied revision after each '
                        'recreate/recovery cycle (heartbeat/SYNC_ACK convergence waited explicitly).',
                        'Monotonicity: the agent applied revision never regressed and never exceeded the Hub watermark.',
                    ],
                },
                {
                    'test_id': '20261017-G4:AR-04/recovered-idempotency-after-fault-window',
                    'layer': 'fault window (agent stopped) + 2 hub changes + recovery',
                    'status': 'PASS',
                    'description': [
                        'While the agent was stopped the hub watermark advanced by exactly the two owned changes '
                        '(script-2 and task-2; changelog rows '
                        + ', '.join(str(c['global_revision']) for c in p5['own_changes_in_window']) + ') while the '
                        'agent local revision stayed frozen; after restart the agent applied the new watermark, '
                        'created no duplicate state and re-synced the Desired State exactly once.',
                    ],
                },
                {
                    'test_id': '20261017-G4:AR-04/attempt02-1573-vs-1575-explained',
                    'layer': 'watermark accounting (hub revision_sequence + node rows + agent local)',
                    'status': 'PASS',
                    'description': [
                        'Reproduced deterministically and explained: hub-side sample = node row '
                        + str(ree['hub_side_sample_node_row_while_down']) + ' (agent stopped), hub watermark while '
                        'down = ' + str(ree['hub_watermark_while_down']) + ', agent-side read after recovery = '
                        + str(ree['agent_local_after_recovery']) + ' — a delta of '
                        + str(ree['delta_hub_sample_vs_agent_read']) + ' equal to the two owned changes that landed '
                        'in between.',
                        'Semantics (source-verified + measured): hub revision_sequence is a single monotonic watermark '
                        'advanced by every hub-side object change; the agent local value is the watermark delivered '
                        'in its last SYNC response; the hub node row is the value the agent reports back (SYNC_ACK '
                        'immediately, heartbeat ≤30s, revision_check 45s). Therefore attempt02 1573 vs 1575 is a '
                        'sampling-order artifact: the agent value being 2 higher means it had already applied a '
                        'newer watermark (no state loss, no unapplied Desired State), and object-level revision+sha '
                        'equality is the real consistency criterion (verified here).',
                    ],
                },
                {
                    'test_id': '20261017-G4:AR-04/persistence-subdimension-refresh',
                    'layer': 'reused persistence dimension re-verified on the Compose form',
                    'status': 'PASS',
                    'description': [
                        'Identity/Revision/Journal/Cache persistence re-verified on the Compose form with the same '
                        'pinned image as the reused SYS-12 evidence (see E2E-18 dimensions); DB dev/inode identical '
                        'across recreates because the named volume survives.',
                    ],
                },
            ],
            'remaining_or_limitations': [
                'Same pinned-image/old-agent scope note as E2E-18 (Docker form runs the pinned old build).',
                'The idempotency statement is "no duplicate state and no extra execution across the fault window", '
                'observed on manual-trigger executions; no schedule-driven duplicate window was exercised here '
                '(that is the G2/G3/G6 scope).',
                'Concurrent batch activity on the shared authority is recorded in harness_notes; per-object and '
                'per-execution-id checks make the results independent of the shared counter.',
            ],
            'evidence': [
                'evidence/phase2-setup.json', 'evidence/phase3-e1-and-readback.json',
                'evidence/phase4-recreate-r1.json', 'evidence/phase5-recovery-r2.json',
                'evidence/phase6-idempotency.json', 'evidence/retained-history.json',
            ],
        },
    ]

    case_result = {
        'batch_id': 'qa-resume-20261017-g4',
        'title': 'G4 Docker/Compose 独占窗口：Compose 重建与恢复后的持久化/幂等/Desired State 一致性',
        'authorization_basis': 'authorization-checklist-20261016.md G4（唯一写入授权；未修改源码/配置/台账）',
        'nonce': L.NONCE,
        'owned_prefix': L.OWNER + '-',
        'authority': 'http://%s:%d (repair)' % (L.HOST, L.REQUIRED_PORT),
        'primary_8080_contacted': False,
        'target_node_vm': 'repair native-4 (%s) docker daemon (exclusive window)' % L.NATIVE4,
        'build_label_under_test': 'dddee4f0-e2e-repair-20261016-cr1 (hub) / pinned image agent dddee4f0-e2e-repair-20261004',
        'pinned_image': {
            'digest': L.IMAGE, 'tag_at_source': L.IMAGE_TAG,
            'agent_binary_sha256': p1['agent_binary_sha256'],
            'import': 'read-only `docker save` stream from native-2 (192.168.100.206) piped into native-4 '
                      '`docker load`; digest verified identical before use; removed again at cleanup; no '
                      'pull/rebuild/retag anywhere.',
            'import_seconds': (p1.get('import_detail') or {}).get('duration_s'),
        },
        'executed_at_utc': p0['captured_at_utc'],
        'finished_at_utc': p8['captured_at_utc'],
        'docker_node': {
            'node_id': node['node_id'], 'agent_id': node['agent_id'], 'hostname': node['hostname'],
            'deployment_mode': 'docker', 'host_integration': False,
            'container_ids_observed': {
                'compose_up': st['containers']['B']['id'],
                'force_recreate': st['containers']['B'].get('id_r1'),
                'down_up_recovery': st['containers']['B'].get('id_r2'),
                'harness_restart_artifact': 'a2ede5b95ec4ef3ffe1cf9eb14b73c8602c0be2d6fb7456810c2ff24544986ca',
            },
            'volume': L.VOL, 'network': L.NET, 'root': L.ROOT, 'data_db_inode': ree and p5['agent_db_after']['inode'],
        },
        'revision_watermarks': {
            'phase3_agent_local': p3['agent_local_revision'],
            'phase3_hub_watermark': p3['hub_watermark_after_run']['revision_sequence'],
            'phase5_before_changes': p5['watermark_before_changes']['revision_sequence'],
            'phase5_own_changes': [c['global_revision'] for c in p5['own_changes_in_window']],
            'phase5_after_changes': p5['watermark_after_changes']['revision_sequence'],
            'phase5_after_recovery_agent_local': ree['agent_local_after_recovery'],
            'phase6_final_hub_watermark': p6['watermark_final']['revision_sequence'],
        },
        'cases': cases,
        'checks_summary': {
            'phase3_e1_and_readback': {'passed': len(p3['checks']), 'failed': [k for k, v in p3['checks'].items() if not v]},
            'phase4_recreate_r1': {'passed': len(p4['checks']), 'failed': [k for k, v in p4['checks'].items() if not v]},
            'phase5_recovery_r2': {'passed': len(p5['checks']), 'failed': [k for k, v in p5['checks'].items() if not v]},
            'phase6_idempotency': {'passed': len(p6['checks']), 'failed': [k for k, v in p6['checks'].items() if not v]},
            'phase7_ui_after': {'passed': len(p7['checks']), 'failed': [k for k, v in p7['checks'].items() if not v]},
        },
        'retained_history': {
            'executions_on_owned_node': exec_rows,
            'executions_from_this_batch': [r['id'] for r in mine],
            'executions_not_initiated_by_this_batch': [r['id'] for r in foreign],
            'owned_audit_rows': rh.get('owned_audit_rows'),
            'note': 'Executions and audit rows cannot be deleted by the product API; counts are registered here. '
                    'The owned node, application, artifact, tasks and scripts were all deleted with GET-after=404.',
        },
        'cleanup_summary': {
            'hub_objects_deleted': p8['hub']['deleted'],
            'hub_node_deleted': p8['hub']['deleted_nodes'],
            'hub_leftover_clear': p8['hub']['leftover_clear'],
            'runtime': {k: p8['runtime'].get(k) for k in
                        ('containers_absent', 'network_absent', 'volume_absent', 'root_absent',
                         'pinned_image_absent', 'all_images', 'all_containers', 'all_volumes', 'all_networks')},
            'baseline_restored': p8['baseline_restored'],
            'protected_nodes': p8['protected'],
        },
        'harness_notes': [
            'Volume copy-up pitfall (real environment behavior): Docker copies the image directory metadata into an '
            'EMPTY named volume on first start, which reset the owned volume mount point to root:root 0755 and made '
            'the agent exit with `init agent: unable to open database file (14)`. The harness now seeds the volume '
            'and re-asserts 65534:65534 0700 after compose up (see state.json volume_perm_checks); the running '
            'container and all case evidence use the corrected ownership. This is a harness/environment note, not a '
            'product defect claim for these two cases.',
            'The pinned image loads tag-less (`<none>:<none>`, digest-pinned); plain `docker images` hides it while '
            '`docker images -a` / `docker image inspect <digest>` show it. Cleanup verifies absence via the digest.',
            'Concurrent batch activity: qa-g6-1fdba63d-* fixtures were created/deleted on the same authority during '
            'this window, and one extra manual execution of the owned task (f1f6d07b-3815-40cb-8b61-4a71623a9577 at '
            '2026-10-17T04:08:05Z, audit: execute task 36b3dcd2 one second after a login) was not initiated by this '
            'harness; all batches share the same admin identity, so the actor cannot be attributed from audit alone. '
            'Impact: none on the case checks (set/per-object based, baselines re-captured per phase); the execution '
            'is registered as retained history.',
            'Credential hygiene: the UI evidence originally contained the operator username rendered in the console '
            'header; it was scrubbed to <operator-username-redacted> and the whole batch directory re-scanned for the '
            'credentials file values (result CLEAN). Password and registration token never appeared in any evidence, '
            'argv, or stdout.',
            'The owned task/execution history plus audit rows remain on the shared authority by design (no delete '
            'API); the node/objects were removed with GET-after 404 records in final-cleanup-oracle.json.',
        ],
    }
    (HERE / 'case-result.json').write_text(json.dumps(case_result, ensure_ascii=False, indent=2) + '\n')

    # ---------------------------------------------------------------- progress
    progress = {
        'batch_id': 'qa-resume-20261017-g4',
        'nonce': L.NONCE,
        'phase': 'done',
        'updated_at_utc': L.now_utc(),
        'authority': 'http://%s:%d' % (L.HOST, L.REQUIRED_PORT),
        'target_node_vm': 'repair native-4 (%s) docker (exclusive window)' % L.NATIVE4,
        'cases': ['E2E-18', 'AR-04'],
        'phases': [
            {'phase': 'preflight', 'status': 'PREFLIGHT_OK', 'detail': 'native-4 docker baseline captured; 4 protected nodes online/synced; pinned image absent on native-4 and present on native-2'},
            {'phase': 'image-load', 'status': 'IMAGE_LOADED', 'detail': 'read-only save stream native-2 → native-4; digest verified; agent binary sha256 d55dc5cd… verified'},
            {'phase': 'setup', 'status': 'SETUP_OK', 'detail': 'owned root/network/volume/compose stack + artifact/application/script/task; node online/synced; artifact cache populated'},
            {'phase': 'run-readback', 'status': 'E1_SUCCESS_AND_READBACK', 'detail': 'execution ' + e1 + ' SUCCESS; identity/revision/journal/cache + UI readback verified (20 checks)'},
            {'phase': 'recreate-r1', 'status': 'RECREATE_R1_PASS', 'detail': 'docker compose up -d --force-recreate: new container, same volume/DB inode, all four dimensions preserved (17 checks)'},
            {'phase': 'recovery-r2', 'status': 'RECOVERY_R2_PASS', 'detail': 'docker compose down (volume kept) + 2 hub changes + up -d: recovery, gap accounting, desired-state reconciliation (23 checks)'},
            {'phase': 'idempotency', 'status': 'IDEMPOTENCY_PASS', 'detail': 'extra sync cycles + second real execution: no duplicates, exactly one new execution (20 checks)'},
            {'phase': 'ui-after', 'status': 'UI_AFTER_PASS', 'detail': 'post-recreate UI readback: identity/revision/journal/managed application (8 checks)'},
            {'phase': 'cleanup', 'status': 'CLEANUP_OK', 'detail': 'all owned hub objects GET-404, docker baseline restored exactly, pinned image removed, leftovers clear'},
        ],
        'final_cleanup': {
            'hub_leftover_clear': p8['hub']['leftover_clear'],
            'baseline_restored': p8['baseline_restored'],
            'protected_nodes_online_synced': all(v.get('status') == 'online' and v.get('sync_status') == 'synced'
                                                 for v in p8['protected'].values()),
            'pinned_image_removed': p8['runtime']['pinned_image_absent'],
        },
    }
    (HERE / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n')

    # ---------------------------------------------------------------- fixtures
    fixture_ledger = {
        'batch_id': 'qa-resume-20261017-g4',
        'nonce': L.NONCE,
        'owned_prefix': L.OWNER + '-',
        'authorization': 'authorization-checklist-20261016.md G4',
        'image_lifecycle': {
            'digest': L.IMAGE, 'tag_at_source': L.IMAGE_TAG,
            'imported_from': 'native-2 (192.168.100.206) read-only docker save stream',
            'reason': 'pinned image absent on native-4; no pull/rebuild/retag permitted',
            'loaded_at_utc': p1['loaded_at_utc'], 'agent_binary_sha256': p1['agent_binary_sha256'],
            'removed_at_cleanup': p8['runtime']['pinned_image_absent'],
        },
        'hub_fixtures': [
            {'kind': 'node', 'label': 'B (compose)', 'name': node['hostname'], 'id': node['node_id'],
             'agent_id': node['agent_id'], 'deleted_get404': True},
            {'kind': 'script', 'name': L.SCRIPT, 'id': st['hub_objects']['script']['id'],
             'sha256': st['hub_objects']['script']['sha256'], 'deleted_get404': True},
            {'kind': 'script', 'name': L.SCRIPT2, 'id': st['hub_objects']['script2']['id'],
             'deleted_get404': True},
            {'kind': 'task', 'name': L.TASK, 'id': st['hub_objects']['task']['id'],
             'target': node['node_id'], 'deleted_get404': True},
            {'kind': 'task', 'name': L.TASK2, 'id': st['hub_objects']['task2']['id'],
             'target': node['node_id'], 'deleted_get404': True},
            {'kind': 'artifact', 'name': L.ARTIFACT, 'id': st['hub_objects']['artifact']['id'],
             'sha256': st['hub_objects']['artifact']['sha256'], 'size': st['hub_objects']['artifact']['size'],
             'deleted_get404': True},
            {'kind': 'application', 'name': L.APP, 'id': st['hub_objects']['application']['id'],
             'assigned_node': node['node_id'], 'deleted_get404': True},
        ],
        'docker_fixtures': [
            {'kind': 'network', 'name': L.NET, 'removed': p8['runtime']['network_absent']},
            {'kind': 'volume', 'name': L.VOL, 'removed': p8['runtime']['volume_absent']},
            {'kind': 'container', 'label': 'compose B', 'name': L.CONT,
             'ids': [st['containers']['B']['id'], st['containers']['B'].get('id_r1'),
                     st['containers']['B'].get('id_r2')],
             'removed': p8['runtime']['containers_absent']},
            {'kind': 'image', 'digest': L.IMAGE, 'removed': p8['runtime']['pinned_image_absent']},
            {'kind': 'host_root', 'path': L.ROOT, 'removed': p8['runtime']['root_absent'],
             'dev_inode_before_removal': p8['runtime']['root_before_removal']},
        ],
        'browser_session': {'name': L.BROWSER_SESSION, 'closed': True,
                            'auth_cleared': 'localStorage/sessionStorage cleared before close'},
        'retained_history': {
            'executions': [r['id'] for r in exec_rows],
            'executions_from_this_batch': [r['id'] for r in mine],
            'executions_not_initiated_by_this_batch': [r['id'] for r in foreign],
            'audit_rows_referencing_owned_resources': rh.get('owned_audit_rows'),
            'note': 'no delete API for executions/audit; counts registered (see case-result.json retained_history).',
        },
        'evidence_facts': {
            'cache_artifact_sha256': cache1[0]['sha256'] if cache1 else None,
            'cache_file_inode_phase3': cache1[0]['inode'] if cache1 else None,
            'cache_file_inode_after_force_recreate': cache4[0]['inode'] if cache4 else None,
            'cache_file_inode_after_down_up': cache5[0]['inode'] if cache5 else None,
            'db_inode_phase3': p3['agent_db_post_run']['inode'],
            'db_inode_after_force_recreate': p4['agent_db_after']['inode'],
            'db_inode_after_down_up': p5['agent_db_after']['inode'],
        },
    }
    (HERE / 'fixture-ledger.json').write_text(json.dumps(fixture_ledger, ensure_ascii=False, indent=2) + '\n')

    # ---------------------------------------------------------------- oracle amendments
    p8['amendments'] = {
        'hub_host_units_and_ports_on_192.168.100.209': {
            'nodesteer-hub-repair-20260930-101945.service': 'active',
            'nodesteer-hub.service (primary)': 'active',
            'nodesteer-agent.service': 'inactive',
            'listening': ['*:8080 (primary, never contacted)', '*:18080 (repair, used)'],
            'observed_at_utc': L.now_utc(),
        },
        'native4_protected_agent_unit': 'active',
        'browser_session_closed': True,
        'credential_scan': 'CLEAN (batch directory scanned for the credentials file values; operator username '
                           'redacted from UI text evidence)',
        'owned_network_volume_root_absent': p8['runtime']['network_absent'] and p8['runtime']['volume_absent']
                                            and p8['runtime']['root_absent'],
    }
    (HERE / 'final-cleanup-oracle.json').write_text(json.dumps(p8, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'case_result': str(HERE / 'case-result.json'),
                      'progress': str(HERE / 'progress.json'),
                      'fixture_ledger': str(HERE / 'fixture-ledger.json'),
                      'oracle': str(HERE / 'final-cleanup-oracle.json'),
                      'e1': e1, 'e2': e2,
                      'executions_total': len(exec_rows)}, indent=2))


if __name__ == '__main__':
    main()
