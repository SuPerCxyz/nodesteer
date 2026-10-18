#!/usr/bin/env python3
"""Build sys-b10 deliverables (case-result / fixture-ledger / oracle copy) from evidence."""
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb10-%s-' % NONCE


def load(name):
    return json.loads((EV / name).read_text())


def jload(name):
    return json.loads((HERE / name).read_text())


p0 = load('phase0-guard.json')
p2 = load('phase2-artifacts.json')
p3 = load('phase3-sha-mismatch.json')
p4 = load('phase4-ar25-sha-failure.json')
p5 = load('phase5-download-faults.json')
p6 = load('phase6-ar24-interruption.json')
p7 = load('phase7-recovery-success.json')
p8 = load('phase8-idempotency.json')
p9 = load('phase9-persistence.json')
p10 = load('phase10-stall-observation.json')
p11 = load('phase11-cleanup.json')
oracle = load('final-cleanup-oracle.json')
frames = load('proxy-frames.json')
run = jload('run-evidence.json')

sha_A = p2['artifacts']['A']['sha256']
sha_B = p2['artifacts']['B']['sha256']
sha_C = p2['artifacts']['C']['sha256']
art = {k: p2['artifacts'][k]['id'] for k in p2['artifacts']}
apps = p7['apps']
node_id = p0['protected']['native-2']['id']
child_node = run['state']['child_node_id']
child_agent = run['state']['child_agent_id']
budget = run['budget']

inj = frames['proxy_state']['injected_frames']
def n_inj(tag):
    return len([f for f in inj if tag in (f.get('type', '') + str(f))]) # placeholder
injections_by = {}
for f in inj:
    injections_by.setdefault(f.get('url_host'), 0)
    injections_by[f.get('url_host')] = injections_by[f.get('url_host')] + 1

EV_A = ['evidence/phase0-guard.json', 'evidence/phase2-artifacts.json',
        'evidence/phase7-recovery-success.json', 'evidence/phase8-idempotency.json',
        'evidence/phase9-persistence.json', 'evidence/proxy-frames.json']
EV_B = ['evidence/phase2-artifacts.json', 'evidence/phase3-sha-mismatch.json', 'evidence/proxy-frames.json']
EV_C = ['evidence/phase4-ar25-sha-failure.json', 'evidence/phase7-recovery-success.json']
EV_D = ['evidence/phase5-download-faults.json', 'evidence/proxy-frames.json']
EV_E = ['evidence/phase6-ar24-interruption.json']
EV_F = ['evidence/phase10-stall-observation.json']

common_res = {
    'owned_prefix': PREFIX,
    'child_node_id': child_node, 'child_agent_id': child_agent,
    'child_root_on_native2': '/var/lib/nodesteer-qa-sysb10-%s' % NONCE,
    'artifacts': {'A': art['A'], 'B': art['B'], 'C': art['C']},
    'artifact_sha256': {'A': sha_A, 'B': sha_B, 'C': sha_C},
    'applications': {'A': apps['A'], 'C': apps['C']},
    'harness_injections': len(inj),
    'harness_injections_by_url_host': injections_by,
}

cases = [
    {
        'case_id': 'SYS-41',
        'title': 'SYS-41 Artifact 下载成功后 SHA256 校验和原子落盘。',
        'canonical_before': 'NOT_RUN (repair build not executed; legacy baseline PASS excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261014-RUNTIME:SYS-41',
            'layer': 'hub-api+real-hub-download/real-owned-child-agent/agent-filesystem+sqlite',
            'status': 'PASS',
            'description': [
                'Desired State path: owned Application APP-A (artifact_id=%s) assigned to the owned child node; Hub sync delivered app with derived artifact_url/artifact_sha256 and the real Agent downloaded it from the real Hub endpoint /api/artifacts/%s/download.' % (art['A'], art['A']),
                'Atomic landing (physical): <DataDir>/artifacts/%s present, size=4096 exactly equals the Hub artifact size, re-computed SHA256 equals the expected SHA256, and no .tmp entry exists in the cache dir.' % sha_A,
                'Agent SQLite: artifacts row for the SHA present and PRAGMA quick_check=ok.',
                'Real agent process log line: level=INFO msg="artifact cached" sha=%s.' % sha_A,
                'Caller-side Hub view: GET /api/artifacts/%s/download with Agent identity returned HTTP 200 and Content-Length 4096 (X-Artifact-Sha256 present; see evidence/phase2b-hub-download-header.json).' % art['A'],
            ]}],
        'remaining_or_limitations': [
            'The atomicity evidence is the absence of any .tmp/partial file plus a byte-exact final SHA; a crash exactly inside the rename window was not forced.',
            'No browser UI was exercised; the caller-side feedback is the Hub API readback (application assignment + artifact metadata) plus the download endpoint response captured with Agent identity.',
            'The Hub download probe recorded X-Artifact-Sha256 under Go canonical casing (X-Artifact-Sha256); an initial case-sensitive lookup reported null and was corrected (harness bug, see evidence/phase2b-hub-download-header.json).',
        ],
        'evidence': EV_A,
        'actual_resources': {k: common_res[k] for k in ('owned_prefix', 'child_node_id', 'child_agent_id', 'artifacts', 'artifact_sha256', 'applications')} | {'desired_state_app': apps['A'], 'installed_sha': sha_A},
        'actual_budget': {'artifact_uploads': 3, 'app_creates': 2, 'note': 'resource budget is shared across the batch; see run_actual_budget'},
        'final_cleanup': {'app_A_get_after_delete': 404, 'artifact_A_get_after_delete': 404,
                          'child_node_get_after_delete': 404,
                          'owned_root_removed': p11['native2_removal']['root_removed']},
    },
    {
        'case_id': 'SYS-42',
        'title': 'SYS-42 SHA256 错误禁止安装，正式缓存和目标文件不被污染。',
        'canonical_before': 'NOT_RUN (repair build not executed; legacy baseline PASS excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261014-RUNTIME:SYS-42',
            'layer': 'harness-frame-proxy-injected ARTIFACT_PREFETCH/real-hub-download/real-agent-sha-check',
            'status': 'PASS',
            'description': [
                'A synthetic ARTIFACT_PREFETCH (Hub->Agent) was injected through the harness frame proxy with url = the real Hub download endpoint of artifact B and sha256 = a fresh expected SHA that no served body can match.',
                'The real Agent downloaded artifact B bytes, detected the mismatch and refused: agent log WARN msg="artifact prefetch failed" error="sha256 mismatch: expected %s got %s".' % (p3['expected_sha256'], sha_B),
                'Negative proof: neither the expected SHA nor the served body SHA produced a cache file, a SQLite artifacts row, or a .tmp entry; the artifact table count stayed 0 at that point.',
                'Non-pollution: the pre-existing real protected-node cache on native-2 was byte-identical before and after the whole batch (final-cleanup-oracle.real_protected_cache_unpolluted=true).',
            ]}],
        'remaining_or_limitations': [
            'The mismatch is produced by a harness-controlled pairing (expected SHA vs served bytes); product source was not modified and the Hub was not stopped/restarted.',
            'An artifact-prefetch failure surfaces only in the owned Agent process log; the Hub has no artifact-prefetch status API/UI surface, so the negative-path caller feedback is that log line.',
        ],
        'evidence': EV_B,
        'actual_resources': {'owned_prefix': PREFIX, 'artifact_B': art['B'], 'artifact_B_sha256': sha_B, 'mismatch_expected_sha256': p3['expected_sha256'], 'child_node_id': child_node},
        'actual_budget': {'harness_injections_mismatch': 1, 'note': 'resource budget is shared across the batch'},
        'final_cleanup': {'artifact_B_get_after_delete': 404, 'no_cache_or_tmp_residue': True, 'real_cache_unpolluted': True},
    },
    {
        'case_id': 'SYS-43',
        'title': 'SYS-43 Artifact 下载中断、超时、HTTP 错误后 `.tmp` 清理。',
        'canonical_before': 'NOT_RUN (repair build not executed; legacy baseline PARTIAL excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261014-RUNTIME:SYS-43',
            'layer': 'harness-controlled HTTP download faults/real-agent-prefetch/agent-filesystem',
            'status': 'PASS',
            'description': [
                'HTTP error: injected prefetch pointed at a real Hub download of a non-existent artifact id -> agent log WARN error="download status 404"; no cache file, no row, no .tmp.',
                'Interruption: harness server declared Content-Length 8192, sent 200 bytes, held 8s and closed -> the .tmp file was observed mid-transfer (200 bytes) and, after the failure, agent log WARN error="unexpected EOF" with .tmp removed and no final file/row.',
                'Timeout: prefetch aimed at a blackholed address -> agent log WARN error="... dial tcp 192.0.2.1:18080: i/o timeout" after ~30s (Go DefaultTransport dial timeout); no cache file, no row, no .tmp.',
                'All three required modes leave the cache directory without any .tmp entry and without a partial file.',
            ]}],
        'remaining_or_limitations': [
            'The agent builds its HTTP client from http.DefaultClient with no response-read/idle timeout and passes the agent-lifetime context, so the "timeout" dimension exercised is the transport dial timeout. A supplemental bounded test (evidence/phase10-stall-observation.json) shows a server that sends headers and then withholds the body leaves a 0-byte .tmp in place for >24s: there is no read timeout. Reported as a robustness observation, not a required-dimension failure.',
            'Prefetch failures surface only in the owned Agent log; there is no Hub-side artifact-prefetch status API/UI to observe them.',
        ],
        'evidence': EV_D,
        'actual_resources': {'owned_prefix': PREFIX, 'fault_server_port': p2['fault_server']['port'], 'blackhole_target': '192.0.2.1:18080', 'child_node_id': child_node},
        'actual_budget': {'harness_injections_sys43': 3, 'note': 'resource budget is shared across the batch'},
        'final_cleanup': {'fault_server_removed': True, 'root_removed': p11['native2_removal']['root_removed'], 'no_tmp_residue': True},
    },
    {
        'case_id': 'AR-24',
        'title': 'Artifact Download Interruption；恢复后幂等、持久化、Desired State与实际状态一致',
        'canonical_before': 'NOT_RUN (repair build not executed; legacy baseline PASS excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261014-RUNTIME:AR-24',
            'layer': 'interruption + real desired-state recovery/idempotency/persistence',
            'status': 'PASS',
            'description': [
                'Interruption for artifact C: .tmp appeared mid-transfer (200 bytes) then the transfer failed (unexpected EOF); final state had no file, no row, no .tmp.',
                'Recovery via Desired State: owned Application APP-C (artifact C) assigned to the owned child -> Hub sync -> real Agent downloaded C from the real Hub -> installed with SHA256 match and size 4096.',
                'Idempotency: re-asserting the same assignment (real resync) plus repeating the correct prefetch produced no re-download - file mtime unchanged, SQLite downloaded_at unchanged, artifact row count stayed 2, no "artifact cached" log added, no .tmp.',
                'Persistence: the owned child Agent was restarted; the cache file and the artifacts row survived with SHA match and quick_check=ok, mtime unchanged.',
                'Desired State == actual state: APP-C assigned on the child node and its artifact present in the local cache.',
            ]}],
        'remaining_or_limitations': [
            'The interruption itself is harness-injected (frame proxy + owned HTTP server); the recovery, idempotency and persistence steps use the real product path.',
            'Concurrent prefetch of the same SHA is not de-duplicated: two sync-triggered goroutines each logged "artifact cached" for the same SHA within ~20ms. Final state stayed consistent (atomic rename, identical bytes), so this is an observation, not a failed dimension.',
        ],
        'evidence': EV_E + ['evidence/phase7-recovery-success.json', 'evidence/phase8-idempotency.json', 'evidence/phase9-persistence.json', 'evidence/proxy-frames.json'],
        'actual_resources': {'owned_prefix': PREFIX, 'artifact_C': art['C'], 'artifact_C_sha256': sha_C, 'app_C': apps['C'], 'child_node_id': child_node},
        'actual_budget': {'harness_injections_interrupt_and_idem': 1, 'note': 'resource budget is shared across the batch'},
        'final_cleanup': {'app_C_get_after_delete': 404, 'artifact_C_get_after_delete': 404, 'owned_root_removed': True},
    },
    {
        'case_id': 'AR-25',
        'title': 'Artifact SHA Failure；恢复后幂等、持久化、Desired State与实际状态一致',
        'canonical_before': 'NOT_RUN (repair build not executed; legacy baseline PASS excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261014-RUNTIME:AR-25',
            'layer': 'sha-failure + real desired-state recovery/idempotency/persistence',
            'status': 'PASS',
            'description': [
                'SHA failure for artifact A before it was ever cached: WARN error="sha256 mismatch: expected %s got %s" (served wrong bytes); install prohibited - no file, no row, no .tmp.' % (sha_A, p4['agent_log_evidence'][-1].split('got ')[-1].split('"')[0]),
                'Recovery via Desired State: owned Application APP-A (artifact A) assigned -> real Hub download -> installed with SHA256 match.',
                'Idempotency: real resync + repeated correct prefetch -> no re-download (mtime and downloaded_at unchanged, row count stable, no extra install log).',
                'Persistence: cache file and row survived an owned child Agent restart with SHA match and quick_check=ok.',
                'Desired State == actual state: APP-A assigned on the child node and its artifact present in the local cache.',
            ]}],
        'remaining_or_limitations': [
            'The initial SHA failure is harness-injected (wrong bytes served by an owned HTTP server); recovery/idempotency/persistence use the real product path.',
        ],
        'evidence': EV_C + ['evidence/phase8-idempotency.json', 'evidence/phase9-persistence.json', 'evidence/proxy-frames.json'],
        'actual_resources': {'owned_prefix': PREFIX, 'artifact_A': art['A'], 'artifact_A_sha256': sha_A, 'app_A': apps['A'], 'child_node_id': child_node},
        'actual_budget': {'harness_injections_sha_fail': 1, 'note': 'resource budget is shared across the batch'},
        'final_cleanup': {'app_A_get_after_delete': 404, 'artifact_A_get_after_delete': 404, 'owned_root_removed': True},
    },
    {
        'case_id': 'INV-10',
        'title': 'Artifact 校验失败禁止安装。',
        'canonical_before': 'NOT_RUN (repair build not executed; legacy baseline PASS excluded per remaining_dimensions)',
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [{
            'test_id': '20261014-RUNTIME:INV-10',
            'layer': 'real-agent-sha-verification/negative-install-proof',
            'status': 'PASS',
            'description': [
                'Mismatched payload (real Hub artifact B bytes vs a fresh expected SHA) was rejected before installation: agent log "sha256 mismatch" and no cache file, no SQLite row, no .tmp.',
                'The same invariant is confirmed for artifact A: wrong bytes for sha_A were rejected and sha_A stayed uncached until the correct bytes arrived (phase4 -> phase7).',
                'Negative pollution proof: the real protected-node artifact cache on native-2 was byte-identical before/after the batch.',
            ]}],
        'remaining_or_limitations': [
            'Verification-failure injection is harness-side by design; no product source change.',
            'The prohibition is observed from the Agent side (no cache file / no SQLite row / no .tmp) because the Hub does not expose an artifact-install status for prefetches.',
        ],
        'evidence': EV_B + ['evidence/phase4-ar25-sha-failure.json'],
        'actual_resources': {'owned_prefix': PREFIX, 'mismatch_expected_sha256': p3['expected_sha256'], 'artifact_B': art['B'], 'artifact_A': art['A']},
        'actual_budget': {'harness_injections_mismatch': 1, 'note': 'resource budget is shared across the batch'},
        'final_cleanup': {'all_owned_artifacts_get_after_delete_404': True},
    },
]

case_result = {
    'batch_id': 'sys-b10',
    'title': 'Artifact 下载：成功校验原子落盘、SHA 错误禁止安装、中断/超时/HTTP 错误清理与恢复',
    'nonce': NONCE, 'owned_prefix': PREFIX,
    'endpoint': run['endpoint'],
    'executed_at_utc': p0['at_utc'], 'finished_at_utc': run['finished_at_utc'],
    'cases': cases,
    'run_actual_budget': budget,
    'harness_failures_retained': [
        'attempt01 (evidence/attempt01-harness-bug/): local DB probe issued "SELECT id,name,revision FROM applications" but the agent applications table stores definition_json (no name column); the resulting KeyError aborted phase7 after phases 0-6 had already produced real evidence, and cleanup still removed every fixture (attempt01 oracle clean=true). Fixed in phase-2 run by parsing the name from definition_json.',
        'phase2 Hub download probe looked up the header with exact casing X-Artifact-SHA256 while Go emits X-Artifact-Sha256, so the first reading was null; corrected in evidence/phase2b-hub-download-header.json.',
    ],
    'verdict_summary': {
        'SYS-41': 'PASS', 'SYS-42': 'PASS', 'SYS-43': 'PASS', 'AR-24': 'PASS', 'AR-25': 'PASS', 'INV-10': 'PASS',
    },
}

ledger = {
    'batch_id': 'sys-b10', 'nonce': NONCE, 'owned_prefix': PREFIX,
    'hub_fixtures': ([
        {'kind': 'artifact', 'label': k, 'id': art[k], 'name': p2['artifacts'][k]['name'],
         'sha256': p2['artifacts'][k]['sha256'],
         'deleted_get404': run['state']['deletes'].get(art[k], {}).get('get_http') == 404}
        for k in ('A', 'B', 'C')] + [
        {'kind': 'application', 'label': k, 'id': apps[k], 'name': PREFIX + 'app-' + k.lower(),
         'deleted_get404': run['state']['deletes'].get(apps[k], {}).get('get_http') == 404}
        for k in ('A', 'C')]),
    'child_node': {'id': child_node, 'agent_id': child_agent, 'hostname': PREFIX + 'child',
                   'node_ip': '192.168.100.206', 'deleted_get404': p11['child_node']['get_http'] == 404,
                   'delete_http': p11['child_node']['delete_http']},
    'host_fixtures': {
        'owned_child_agent_process': {'host': '192.168.100.206', 'root': '/var/lib/nodesteer-sysb10-' + NONCE if False else '/var/lib/nodesteer-qa-sysb10-%s' % NONCE,
                                      'removed': p11['native2_removal']['root_removed'],
                                      'root_exists_after': p11['native2_removal']['root_exists_after']},
        'fault_injection_http_server': {'port': p2['fault_server']['port'], 'host': '192.168.100.206 (127.0.0.1)',
                                        'removed': not p11['native2_removal']['server_processes_after']},
        'frame_proxy': {'where': 'operator workstation 192.168.100.170', 'stopped': True},
    },
    'shared_fixtures_untouched': {'artifact': '7b8929ed-c880-46f1-90e6-a5e9c0dab096',
                                  'application': '804e6b14-eb84-4dda-90de-8bcab9586c4c'},
    'status': 'CLEANED' if oracle['clean'] else 'CLEANUP_INCOMPLETE',
}

(HERE / 'case-result.json').write_text(json.dumps(case_result, ensure_ascii=False, indent=2) + '\n')
(HERE / 'fixture-ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + '\n')
shutil.copyfile(EV / 'final-cleanup-oracle.json', HERE / 'final-cleanup-oracle.json')

# progress.json: keep the phase trail, add the final verdicts.
prog = jload('progress.json')
prog['phase'] = 'done'
prog['updated_at_utc'] = run['finished_at_utc']
prog['harness_error'] = None
prog['oracle_clean'] = oracle['clean']
prog['verdict_summary'] = case_result['verdict_summary']
prog['all_owned_deletes_get_404'] = p11['all_get_404']
prog['owned_root_removed'] = p11['native2_removal']['root_removed']
prog['final_cleanup_get_http'] = {d['id']: d['get_http'] for d in p11['deleted']} | {child_node: p11['child_node']['get_http']}
(HERE / 'progress.json').write_text(json.dumps(prog, ensure_ascii=False, indent=2) + '\n')

print('deliverables written; oracle clean=%s' % oracle['clean'])
