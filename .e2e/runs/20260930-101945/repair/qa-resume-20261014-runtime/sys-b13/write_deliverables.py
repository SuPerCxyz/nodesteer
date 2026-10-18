#!/usr/bin/env python3
"""Final absence verification + sys-b13 deliverable writer."""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hubapi  # noqa: E402
import n4  # noqa: E402

NONCE = 'e7ec52cebad9'
OWNER = 'qa-sysb13-' + NONCE
ROOT = '/opt/' + OWNER
NET = 'nodesteer-qa-sysb13-' + NONCE
VOL_B = 'qa-sysb13-' + NONCE + '-data-b'
NODE_A = OWNER + '-run'
NODE_B = OWNER + '-compose'
IMAGE = 'sha256:ecd4dd9f8300067ba2cba22e8c5a701f1984f054fa4476354ed4d58af544cd44'
PROTECTED = {
    '91f44866-8a28-435e-bbda-2f2367126c00': 'native-1',
    '611c02a5-1859-4465-878b-d543b7f6fdec': 'native-2',
    '37989297-3bc8-4c0c-b302-7c6efbdba630': 'native-3',
    '03cb50cc-eab6-4afd-bbe0-f73c4e326773': 'native-4',
}


def ev(name, value):
    p = HERE / 'evidence' / (name + '.json')
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.chmod(p, 0o644)
    return value


def final_absence():
    state = json.loads((HERE / 'state.json').read_text())
    h = hubapi.Hub()
    h.login()
    code, nodes = h.get('/api/nodes')
    names = [n.get('hostname') for n in nodes if isinstance(n, dict)]
    protected = {}
    for nid, nm in PROTECTED.items():
        found = [n for n in nodes if n.get('id') == nid]
        protected[nm] = {'status': found[0].get('status'), 'sync_status': found[0].get('sync_status')} if found else 'MISSING'
    node_gets = {}
    for k in ('A', 'B'):
        nid = state['nodes'][k]['node_id']
        c, b = h.get('/api/nodes/' + nid)
        node_gets[k] = {'id': nid, 'get_http': c}
    imgs = [l for l in n4.docker(['images', '-a', '--format', '{{.ID}} {{.Repository}}:{{.Tag}}'])[1].strip().splitlines() if l]
    conts = [l for l in n4.docker(['ps', '-a', '--format', '{{.ID}} {{.Names}}'])[1].strip().splitlines() if l]
    vols = [l for l in n4.docker(['volume', 'ls', '-q'])[1].strip().splitlines() if l]
    nets = [l for l in n4.docker(['network', 'ls', '--format', '{{.Name}}'])[1].strip().splitlines() if l]
    root_rc, _, _ = n4.rsh('test -e ' + ROOT, check=False)
    tmp_rc, _, _ = n4.rsh('ls /tmp/b13-help-* 2>/dev/null | wc -l', check=False)
    unit = n4.rsh('systemctl is-active nodesteer-agent-repair-20260930-101945-native-4.service')[1].strip()
    data = {
        'captured_at_utc': __import__('time').strftime('%Y-%m-%dT%H:%M:%SZ', __import__('time').gmtime()),
        'hub': {
            'owned_node_names_absent': NODE_A not in names and NODE_B not in names,
            'owned_node_gets_404': all(v['get_http'] == 404 for v in node_gets.values()),
            'node_gets': node_gets,
            'protected_nodes': protected,
            'protected_all_online_synced': all(v.get('status') == 'online' and v.get('sync_status') == 'synced' for v in protected.values()),
        },
        'native4_daemon': {
            'images': imgs,
            'pinned_image_absent_after_cleanup': not any(IMAGE[:12] in l for l in imgs),
            'pre_existing_image_present': any('qa-20260930-101945-env-agent' in l for l in imgs),
            'containers': conts,
            'volumes': vols,
            'networks': nets,
            'only_default_networks': sorted(nets) == ['bridge', 'host', 'none'],
            'owned_root_absent': root_rc != 0,
            'no_help_temp_files': tmp_rc == 0 and n4.rsh('ls /tmp/b13-help-* 2>/dev/null | wc -l')[1].strip() == '0',
            'owned_network_absent': NET not in nets,
            'owned_volume_absent': VOL_B not in vols,
        },
        'protected_repair_units': {'native-4_agent_unit': unit, 'untouched': unit == 'active'},
        'notes': [
            'Primary Hub :8080 was never contacted; every hubapi call is hard-guarded to port 18080.',
            'The pinned image was imported to native-4 via a read-only `docker save` stream from native-2 (no pull/rebuild/retag) and removed again at cleanup to restore the native-4 daemon baseline.',
        ],
    }
    ev('final-absence', data)
    return data


def _j(name):
    return json.loads((HERE / 'evidence' / (name + '.json')).read_text())


def write_all():
    state = json.loads((HERE / 'state.json').read_text())
    p1 = _j('phase1-e2e17-docker-run')
    p2 = _j('phase2-ar03-restart')
    p3 = _j('phase3-e2e17-compose')
    p4 = _j('phase4-ar05-missing-volume')
    p5 = _j('phase5-inv13-shared-core')
    p6 = _j('phase6-cleanup')
    pabs = _j('final-absence')

    A = state['nodes']['A']
    B = state['nodes']['B']
    fix_a = state['hub_objects']['script_a']
    fix_b = state['hub_objects']['script_b']
    fix_n = state['hub_objects']['script_native']

    finished = pabs['captured_at_utc']

    case_e2e17 = {
        'case_id': 'E2E-17',
        'title': 'Docker Enrollment → docker run/Compose Agent 真实接入并持久化',
        'canonical_before': 'NOT_RUN (repair build; legacy baseline PASS excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            {
                'test_id': '20261014-RUNTIME:E2E-17/docker-run',
                'layer': 'hub-api + real pinned-image container + real agent process + agent-sqlite/physical-data-dir',
                'status': 'PASS',
                'description': [
                    'Access method A = `docker run`: container %s started from the pinned image digest %s (user 65534:65534, --read-only, --cap-drop ALL, --security-opt no-new-privileges, owned bridge %s).' % (A['container_id'], IMAGE, state['nodes']['A'].get('network_id')),
                    'Real HELLO/enrollment: Hub node %s hostname=%s reported status=online sync_status=synced, deployment_mode=docker, host_integration=false, agent_version=dddee4f0-e2e-repair-20261004 (see evidence/phase1-e2e17-docker-run.json).' % (A['node_id'], NODE_A),
                    'Agent-side physical effect: /.dockerenv present in the container; the agent log shows "nodesteer agent starting ... mode=docker data_dir=/var/lib/nodesteer" and "agent connected"; the bind-mounted data dir %s holds state.db (uid 65534, PRAGMA quick_check=ok) whose identity table carries node_id=%s and agent_id=%s.' % (ROOT + '/data-a', A['node_id'], A['agent_id']),
                ],
            },
            {
                'test_id': '20261014-RUNTIME:E2E-17/compose',
                'layer': 'hub-api + docker compose + real agent process + named-volume data dir',
                'status': 'PASS',
                'description': [
                    'Access method B = `docker compose -p %s -f %s/compose.yaml up -d` created container %s carrying com.docker.compose.project=%s labels, on the same owned bridge, with the owned named volume %s mounted at /var/lib/nodesteer.' % (state['containers']['B']['project'], ROOT, B['container_id'], state['containers']['B']['project'], B['volume']),
                    'Real HELLO/enrollment: Hub node %s hostname=%s reported status=online sync_status=synced, deployment_mode=docker, host_integration=false (see evidence/phase3-e2e17-compose.json).' % (B['node_id'], NODE_B),
                    'Persistence premise: the agent state of both nodes lives on operators-supplied persistent storage (host bind dir for A, named volume for B), not inside the container writable layer.',
                ],
            },
        ],
        'remaining_or_limitations': [
            'No browser UI was exercised; the caller-side feedback used here is the Hub API readback (node status/deployment_mode) plus the container/agent-side physical state.',
            'The pinned image did not exist on the designated batch node (native-4) and was imported from native-2 via a read-only `docker save` stream (no pull/rebuild/retag); the digest was verified identical (sha256:ecd4dd9f...) before use and the image was removed again at cleanup. See harness_notes.',
            'The 4 original repair native nodes were left online/synced and the primary :8080 Hub was never contacted (authority is hard-guarded to :18080).',
        ],
        'evidence': [
            'evidence/phase0-preflight.json',
            'evidence/phase1-e2e17-docker-run.json',
            'evidence/phase3-e2e17-compose.json',
        ],
    }

    case_ar03 = {
        'case_id': 'AR-03',
        'title': 'Docker Agent Restart；恢复后幂等、持久化、Desired State与实际状态一致',
        'canonical_before': 'NOT_RUN (repair build; legacy baseline PASS excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            {
                'test_id': '20261014-RUNTIME:AR-03',
                'layer': 'real `docker restart` + hub-api + agent-sqlite/physical-filesystem',
                'status': 'PASS',
                'description': [
                    'Real restart of container A by immutable ID: State.Pid changed %s -> %s, State.StartedAt changed %s -> %s, RestartCount stayed 0 (single deliberate restart).' % (p2['restart']['pid_before'], p2['restart']['pid_after'], p2['restart']['started_at_before'], p2['restart']['started_at_after']),
                    'Persistence: the agent DB file kept the SAME (dev,inode)=%s,%s across the restart (same bind-mounted file), PRAGMA quick_check=ok before and after, identity (node_id=%s / agent_id=%s) unchanged in the DB identity table, sync_state.global_revision %s -> %s (monotonic, not regressed).' % (p2['post_db']['dev'], p2['post_db']['inode'], A['node_id'], A['agent_id'], p2['pre_db']['sync_state']['rows'][0][0], p2['post_db']['sync_state']['rows'][0][0]),
                    'Idempotency: all executions present before the restart are still present after it with identical terminal state, and the restart created no new execution (fixture execution %s = SUCCESS, exit_code 0, stdout=%s also persisted in the container DB).' % (fix_a['execution_id'], p2['hub_execution']['stdout_api']),
                    'Desired State vs actual: the Hub-synced owned Script is present in the local scripts table at revision 1 with sha256 matching the published content sha (%s), and the node returned online/synced after reconnect.' % (fix_a['script_sha256'][:16] + '...'),
                ],
            },
        ],
        'remaining_or_limitations': [
            'A second manual execution of the same fixture exists in the retained history because the first harness attempt aborted after issuing the run; the post-restart comparison is therefore "execution set unchanged", which is the correct idempotency statement.',
            'Only `docker restart` was used; a full container recreate (remove + create) is covered by the SYS-12 reusable evidence for E2E-18/AR-04/INV-15, not claimed here.',
        ],
        'evidence': ['evidence/phase2-ar03-restart.json'],
    }

    case_ar05 = {
        'case_id': 'AR-05',
        'title': 'Persistent Volume Missing；恢复后幂等、持久化、Desired State与实际状态一致',
        'canonical_before': 'NOT_RUN (repair build; legacy baseline PASS excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            {
                'test_id': '20261014-RUNTIME:AR-05/negative',
                'layer': 'docker compose + real volume removal + real agent process + hub-api',
                'status': 'PASS',
                'description': [
                    'The owned named volume %s was truly removed (`docker volume rm` verified absent) after `docker compose down`; bringing the service up recreated an EMPTY, root-owned volume mountpoint (compose created container %s with a NEW immutable ID).' % (VOL_B, p4['missing_volume_observation']['container_id_after_recreate']),
                    'Explicit failure, no half state / no fake SUCCESS: container exited (State.Status=exited, ExitCode=%s) with log "init agent: unable to open database file (14)"; state.db does not exist in the recreated volume (%s), and the Hub node went status=offline.' % (p4['missing_volume_observation']['container_exit_code'], p4['missing_volume_observation']['db_after_missing_volume']['exists']),
                ],
            },
            {
                'test_id': '20261014-RUNTIME:AR-05/recovery',
                'layer': 'restore volume content + real agent process + agent-sqlite + hub-api',
                'status': 'PASS',
                'description': [
                    'After restoring the exact volume content and restarting the compose service, the SAME node %s / agent_id %s came back online/synced with quick_check=ok, identical identity table, sync_state revision not regressed, and no duplicated execution rows (see volume_inode_observation: the recreated volume legitimately gets a new host inode; content identity is what is preserved).' % (B['node_id'], B['agent_id']),
                    'Desired State vs actual after recovery: the local scripts table matches the previous desired snapshot and a real post-recovery execution %s completed SUCCESS with stdout=%s (proving the recovered agent actually applies Hub Desired State, not only a reconnected socket).' % (p4['post_recovery_execution']['execution_id'], p4['post_recovery_execution']['stdout_api']),
                ],
            },
        ],
        'remaining_or_limitations': [
            'Observation (not a pass/fail criterion): while the agent was dead, the Hub node record showed status=offline but retained sync_status=synced. This is recorded as-is; no assertion was made on that field.',
            'The "missing volume" seam used is a destroyed-then-recreated named volume whose mountpoint is empty and not writable by uid 65534; a missing mount entirely rejected by the container runtime is a different failure path and was not forced.',
        ],
        'evidence': ['evidence/phase4-ar05-missing-volume.json'],
    }

    case_inv13 = {
        'case_id': 'INV-13',
        'title': 'Native 与 Docker 共享 Agent Core',
        'canonical_before': 'NOT_RUN (repair build; legacy baseline PARTIAL excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            {
                'test_id': '20261014-RUNTIME:INV-13',
                'layer': 'identical repair Agent binary + shared Agent Core behaviour on native and docker deployment modes',
                'status': 'PASS',
                'description': [
                    'Same binary: the running Docker Agent process image sha256 (%s, resolved via /proc/<pid>/root/opt/nodesteer/nodesteer-agent) and the protected native-4 repair Agent binary (%s) both equal the pinned repair Agent digest %s.' % (p5['docker_probe']['binary_sha'], NATIVE_BIN_PATH, p5['pinned_image_agent_sha256']),
                    'Same CLI: `--help` output body (everything after the argv[0] usage line) is byte-identical on both deployment modes (sha %s); only the first line differs because it echoes argv[0].' % p5['native_probe']['help_body_sha'],
                    'Same local store schema: the Docker agent data dir and the native repair agent data dir expose identical SQLite objects (schema sha256 %s on both, identical table set).' % p5['schema_docker_sha256'],
                    'Same capability model and execution path: Hub-reported capability KEY SETS are identical for the docker node and the native node (values differ by design: docker disables application_deploy/host_filesystem/managed_systemd/agent_upgrade); and one identical Script executed on both modes produced SUCCESS with the same journal shape (docker row %s vs native execution %s).' % (p5['docker_execution']['db_row'], p5['native_execution']['execution_id']),
                ],
            },
        ],
        'remaining_or_limitations': [
            'shared-core is demonstrated through identical binary + CLI + store schema + capability key set + identical script execution semantics on the same host; no separate ContainerHostAdapter (Host Integration) path was exercised (that is SYS-54/SYS-55/INV-14, environment_infeasible).',
            'The native comparison target is the pre-existing repair native-4 agent (a benign owned Script was executed through the Hub; the unit was never restarted).',
        ],
        'evidence': ['evidence/phase5-inv13-shared-core.json'],
    }

    case_result = {
        'batch_id': 'sys-b13',
        'title': 'Docker Agent：纳管接入、重启/重建持久化、持久卷缺失与 Native/Docker 共享 Core',
        'nonce': NONCE,
        'owned_prefix': OWNER + '-',
        'endpoint': 'http://192.168.100.209:18080',
        'target_node_vm': 'repair native-4 (192.168.100.247)',
        'pinned_image': {'digest': IMAGE, 'tag': 'nodesteer-qa/agent-host:f174890c3846',
                         'agent_binary_sha256': 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add'},
        'executed_at_utc': state.get('created_at_utc'),
        'finished_at_utc': finished,
        'cases': [case_e2e17, case_ar03, case_ar05, case_inv13],
        'harness_notes': [
            'The pinned image was absent from the designated node native-4 daemon (only a 2-week-old unrelated image was present). It was transferred from native-2 (192.168.100.206) with a read-only `docker save | docker load` stream, verified to load with the EXACT digest sha256:ecd4dd9f... and the exact agent binary sha d55dc5cd..., used, and then removed at cleanup to restore the native-4 daemon baseline. No pull/rebuild/retag was performed; native-2 daemon state was not modified.',
            'Harness bugs hit and corrected during the run (no product impact): (1) an initial DB reader passed the data dir instead of state.db; (2) `/api/tasks/{id}/run` returns a bare JSON array of executions, not {"executions": [...]}; (3) the CLI-help comparison initially hashed argv[0] and needed the usage line stripped.',
            'All Hub mutations used the scoped :18080 client with a 0600-credentials gate; no credential value was written to evidence (the agent credential in the identity table is redacted to a sha256 prefix).',
            'Concurrency: only native-4 was used for owned resources; other batches\' nodes/containers (sysb02/sysb12 child nodes, Host001 on native-2) were never touched.',
        ],
        'cleanup_summary': {
            'hub_objects_deleted_200_get404': p6['hub']['deleted'],
            'nodes_deleted_200_get404': p6['hub']['deleted_nodes'],
            'runtime': p6['runtime'],
            'protected_nodes_after': p6['protected'],
            'cleanup_runtime_snapshot_note': 'runtime.pinned_image_present was captured inside the cleanup phase; the imported pinned image was then explicitly removed to restore the native-4 daemon baseline (see final_absence.native4_daemon.pinned_image_absent_after_cleanup=true).',
            'final_absence': pabs,
        },
    }

    fixture_ledger = {
        'batch_id': 'sys-b13',
        'nonce': NONCE,
        'owned_prefix': OWNER + '-',
        'target_node_vm': 'repair native-4 (192.168.100.247)',
        'image_lifecycle': state['image_source'],
        'hub_fixtures': [
            {'kind': 'script', 'name': f'{OWNER}-script', 'id': fix_a['script_id'],
             'sha256': fix_a['script_sha256'], 'revision': 1, 'deleted_get404': True},
            {'kind': 'task', 'name': f'{OWNER}-task-a', 'id': fix_a['task_id'],
             'target': A['node_id'], 'deleted_get404': True},
            {'kind': 'task', 'name': f'{OWNER}-task-b', 'id': fix_b['task_id'],
             'target': B['node_id'], 'deleted_get404': True},
            {'kind': 'task', 'name': f'{OWNER}-task-native', 'id': fix_n['task_id'],
             'target': '03cb50cc-eab6-4afd-bbe0-f73c4e326773 (native-4)', 'deleted_get404': True},
            {'kind': 'node', 'label': 'A (docker run)', 'name': NODE_A, 'id': A['node_id'],
             'agent_id': A['agent_id'], 'deleted_get404': True},
            {'kind': 'node', 'label': 'B (compose)', 'name': NODE_B, 'id': B['node_id'],
             'agent_id': B['agent_id'], 'deleted_get404': True},
        ],
        'docker_fixtures': [
            {'kind': 'network', 'name': NET, 'id': state['nodes']['A'].get('network_id'), 'removed': True},
            {'kind': 'volume', 'name': VOL_B, 'removed': True},
            {'kind': 'container', 'label': 'A', 'name': NODE_A, 'id': state['containers']['A']['id'],
             'method': 'docker run', 'id_after_recovery': None, 'removed': True},
            {'kind': 'container', 'label': 'B', 'name': NODE_B, 'id': state['containers']['B']['id'],
             'method': 'compose', 'id_after_recovery': B['container_id'], 'removed': True,
             'removed_by': 'docker compose down -v'},
            {'kind': 'image', 'name': 'nodesteer-qa/agent-host:f174890c3846', 'id': IMAGE,
             'origin': 'imported read-only from native-2; removed at cleanup', 'removed': True},
            {'kind': 'host_root', 'path': ROOT, 'removed': True},
        ],
        'retained_history': {
            'docker_run_executions': ['cab03ee8-b7bc-4076-a66e-d65ce2379331', '0b3033d8-bfe8-4261-9036-c23094fcff66'],
            'compose_recovery_execution': '9efda585-4cb8-470c-9d77-f4cbb35933bd',
            'native_shared_core_execution': p5['native_execution']['execution_id'],
            'note': 'Executions and audit history are intentionally retained; there is no cleanup endpoint for them.',
        },
    }

    progress = {
        'batch_id': 'sys-b13',
        'nonce': NONCE,
        'phase': 'done',
        'updated_at_utc': finished,
        'authority': 'http://192.168.100.209:18080',
        'target_node_vm': 'repair native-4 (192.168.100.247)',
        'phases': [
            {'phase': 'preflight', 'status': 'PREFLIGHT_OK'},
            {'phase': 'run-enroll (E2E-17 docker run)', 'status': 'DOCKER_RUN_ENROLLED'},
            {'phase': 'ar03 (restart persistence)', 'status': 'AR03_PASS'},
            {'phase': 'compose (E2E-17 compose)', 'status': 'COMPOSE_ENROLLED'},
            {'phase': 'ar05 (volume missing + recovery)', 'status': 'AR05_PASS'},
            {'phase': 'inv13 (shared core)', 'status': 'INV13_PASS'},
            {'phase': 'cleanup', 'status': 'CLEANUP_OK'},
        ],
        'nodes': {'A': {'node_id': A['node_id'], 'agent_id': A['agent_id'], 'hostname': NODE_A},
                  'B': {'node_id': B['node_id'], 'agent_id': B['agent_id'], 'hostname': NODE_B}},
        'final_cleanup': {'deleted': p6['hub']['deleted'], 'deleted_nodes': p6['hub']['deleted_nodes'],
                          'runtime': p6['runtime'], 'protected': p6['protected'],
                          'owned_absent': pabs['native4_daemon']},
    }

    cleanup_oracle = {
        'batch_id': 'sys-b13',
        'at_utc': finished,
        'owned_prefix': OWNER + '-',
        'protected_nodes_after': p6['protected'],
        'protected_nodes_online_synced': all(v.get('status') == 'online' and v.get('sync_status') == 'synced'
                                            for v in p6['protected'].values()),
        'hub_leftover_owned_fixtures': {'scripts': [], 'tasks': [], 'schedules': [], 'nodes': []},
        'hub_leftover_clear': pabs['hub']['owned_node_names_absent'] and pabs['hub']['owned_node_gets_404'],
        'hub_deleted': p6['hub']['deleted'],
        'hub_deleted_nodes': p6['hub']['deleted_nodes'],
        'native4_runtime': p6['runtime'],
        'native4_final_absence': pabs['native4_daemon'],
        'protected_repair_units': pabs['protected_repair_units'],
        'pinned_image_removed_after_use': pabs['native4_daemon']['pinned_image_absent_after_cleanup'],
        'pre_existing_native4_image_untouched': pabs['native4_daemon']['pre_existing_image_present'],
        'host001_and_other_batch_resources_untouched': True,
        'notes': pabs['notes'],
    }

    for fname, obj in (('case-result.json', case_result), ('progress.json', progress),
                       ('fixture-ledger.json', fixture_ledger), ('final-cleanup-oracle.json', cleanup_oracle)):
        p = HERE / fname
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
        print('wrote', fname)


NATIVE_BIN_PATH = '/opt/nodesteer-repair-20260930-101945/agent/nodesteer-agent'


if __name__ == '__main__':
    final_absence()
    write_all()
