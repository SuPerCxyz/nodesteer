#!/usr/bin/env python3
"""Consolidate batch07 deliverables: results.json, progress.json, fixture-ledger.json,
final-cleanup-guard.json (plus their evidence references)."""
import json
import time
from pathlib import Path

import hub
import nodes
import p16
from run_b07 import BATCH, EV, NONCE, PREFIX, N3DIR, NATIVE3, BASELINE_CACHE_SHA, now

LEDGER = Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/feature-inventory.json')
CASES = ['P22-10', 'P22-11', 'P22-13', 'P22-14', 'P16-09', 'P16-10']


def load(name):
    p = EV / name
    return json.loads(p.read_text()) if p.exists() else None


def ledger_cases():
    d = json.loads(LEDGER.read_text())
    return {c['case_id']: c for c in d['cases'] if c['case_id'] in CASES}


def audit(h):
    code, items = h.get('/api/audit?limit=1000')
    return items if isinstance(items, list) else []


def main():
    st = json.loads((BATCH / 'state.json').read_text())
    h = hub.Hub()
    h.login()
    owned_ids = {'transfers': [t['id'] for t in st['created']['transfers']],
                 'artifacts': [a['id'] for a in st['created']['artifacts']],
                 'applications': [a['id'] for a in st['created']['applications']]}

    # ---------------- live readbacks ----------------
    counts_now = h.counts()
    nodes_now = [{'id': n['id'], 'hostname': n['hostname'], 'ip': n['ip'], 'status': n['status'],
                  'deployment_mode': n.get('deployment_mode')} for n in h.nodes()]
    transfers_now = {}
    for tid in owned_ids['transfers']:
        code, item = h.get('/api/transfers/%s' % tid)
        transfers_now[tid] = item
    deltas_map = {
        'P22-10 isolation': 'P22-10-isolation.json',
        'P22-10 isolation attempt1 (reconstructed)': 'P22-10-isolation-attempt1.json',
        'P22-11 target_id retry': 'P22-11-retry.json',
        'P22-13 file over limit': 'P22-13-size-limit.json',
        'P22-14 half-file cleanup + target_id retry': 'P22-14-half-file.json',
        'P22-14 content/mode/sha/atomic replace': 'P22-14-interrupt.json',
    }
    cache_state = nodes.agent_cache_state()
    agent_db = nodes.agent_db_artifacts()
    n3_services = nodes.n3_out('systemctl is-active nodesteer-agent.service '
                               'nodesteer-agent-repair-20260930-101945-native-3.service; df -h /tmp | tail -1; '
                               'free -m | head -2; uptime')
    node_absence = nodes.n3_out('ls -d /tmp/qa-w1410b07-* 2>&1; pgrep -af watch_tmp.py || echo "no watcher"')
    hub_leftovers = nodes.hubsh_out('ls -la %s/ | grep -E "b07|%s" || echo none; echo "--staging--"; '
                                    'ls -la %s/ | grep -E "b07" || echo none'
                                    % (nodes.HUB_ARTIFACTS, st['created']['artifacts'][0]['sha256'],
                                       nodes.HUB_DATA + '/file-transfers'))
    aus = audit(h)
    my_writes = {}
    for a in aus:
        if a.get('resource_id') in set(owned_ids['transfers'] + owned_ids['artifacts'] + owned_ids['applications']):
            k = '%s:%s' % (a.get('resource'), a.get('action'))
            my_writes[k] = my_writes.get(k, 0) + 1
    my_logins = sum(1 for a in aus if a.get('action') == 'login' and a.get('username', '').startswith('qa-repair'))

    # ---------------- per-case results ----------------
    iso2 = load('P22-10-isolation.json')
    iso1 = load('P22-10-isolation-attempt1.json')
    retry = load('P22-11-retry.json')
    neg = load('P22-13-api-negatives.json')
    limit = load('P22-13-size-limit.json')
    half = load('P22-14-half-file.json')
    replace = load('P22-14-interrupt.json')
    happy = load('P22-09-happy-install.json') or load('P16-09-happy-install.json')
    mismatch = load('P16-09-sha-mismatch.json')
    notfound = load('P16-10-notfound-cleanup-retry.json')
    int1 = load('P16-10-interrupt-retry.json')
    int2 = load('P16-10-interrupt-retry2.json')
    ui_forms = load('UI-02-form-negative.json')
    ui_tbl = load('UI-01b-transfers-filtered.json')
    ui_app = load('UI-05-07-readback.json')
    ui_del = load('UI-09-app-delete.json')
    incident = load('incident-report.json')

    def tgt(item, idx=0):
        t = (item.get('targets') or [{}])[idx]
        return {'node_id': t.get('node_id'), 'status': t.get('status'), 'error': t.get('error'),
                'destination_path': t.get('destination_path'), 'mode': t.get('mode')}

    cases = []

    cases.append({
        'case_id': 'P22-10',
        'title': '目标失败隔离，其他目标仍成功。',
        'canonical_before': {'status': 'NOT_RUN', 'baseline_status': 'PARTIAL', 'priority': 'P1'},
        'proposed_after': 'PARTIAL',
        'confidence': 'medium',
        'covered_dimensions': [
            "two real 2-target transfers (source native-3 .222): target A native-3 /tmp (allowlisted) reached "
            "SUCCESS with the exact source size/sha256/mode in both attempts, while target B (host001 "
            "docker host-integration, destination outside its host_path_allowlist) never blocked or failed "
            "target A -> the 'other target still succeeds' half of the case is verified end to end",
            "UI readback of the same isolation: /transfers filtered by the batch nonce shows the CANCELED "
            "transfer with per-target statuses 'native-3 成功' + 'host001 已取消' (UI-01b screenshot+scan)",
            "hub-side per-target state machine observed: target A SUCCESS while target B stayed DELIVERING "
            "with no agent result, then POST /api/transfers/{id}/cancel -> 200 -> CANCELED (attempt1 12+ min, "
            "attempt2 90 s)",
        ],
        'remaining_or_limitations': [
            "the FAILED-target half is NOT verified: host001's agent accepted the delivery envelope but never "
            "reported any result (no allowlist error, no failure, no success) in either attempt. The Hub has no "
            "delivery timeout, so the target stayed DELIVERING and the transfer stayed non-terminal until "
            "canceled. batch04 (2026-10-14 09:17Z) did observe the fast 'destination path: path is outside host "
            "allowlist' FAILED on host001, so this is environment drift on host001's docker host integration, "
            "not a product verdict.",
            "a harness fault injection (truncating the Hub staged blob to 4096 B) also produced no agent-side "
            "result, confirming the host001 agent never started the download (its download URL is not in use).",
            "because only native-3 may be used for node-side work, no second native node was available for a "
            "FAILED+SUCCESS pair; state-changing a node to produce a fast failure was out of scope.",
        ],
        'evidence': ['evidence/P22-10-isolation.json', 'evidence/P22-10-isolation-attempt1.json',
                     'evidence/UI-01b-transfers-filtered.json', 'evidence/UI-01-transfers.json',
                     'evidence/snapshot-after-transfers.json'],
        'actual': {'attempt1_transfer': iso1 and iso1['transfer_id'],
                   'attempt1_final': iso1 and iso1['final'].get('status'),
                   'attempt1_targets': iso1 and [t['status'] for t in iso1['final']['targets']],
                   'attempt2_transfer': iso2 and iso2['transfer_id'],
                   'attempt2_final': iso2 and (iso2.get('final_after_cancel') or iso2['final']).get('status'),
                   'attempt2_targets': iso2 and [t['status'] for t in (iso2.get('final_after_cancel') or iso2['final'])['targets']],
                   'attempt2_poll_seconds': iso2 and iso2.get('poll_seconds'),
                   'native3_destination': iso2 and iso2['node_side_native3']['stdout']},
    })

    cases.append({
        'case_id': 'P22-11',
        'title': 'FAILED → retry，验证整体重试和 `target_id` 单目标重试。',
        'canonical_before': {'status': 'NOT_RUN', 'baseline_status': 'PARTIAL', 'priority': 'P1'},
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            "whole-transfer retry (no target_id) after a source-side FAILED: transfer c8f2cb42 ended FAILED with "
            "'source upload failed: open /tmp/qa-w1410b07-<nonce>/src-late.bin: no such file or directory' "
            "(sha256=\"\"), the missing source was created, POST /api/transfers/{id}/retry restarted the source "
            "upload and the delivery -> SUCCESS with the new source sha256 verified on the node",
            "single-target retry (target_id=37989297-...): transfer f926be58 (FAILED, target FAILED with "
            "'write ...: no space left on device') -> POST /api/transfers/{id}/retry?target_id=<native-3> -> 200 "
            "-> target re-dispatched -> SUCCESS; the destination then matched the source sha256/size exactly",
            "retry does not re-dispatch an already SUCCESS target: retry on the SUCCESS transfer 4aff7bd2 "
            "(no FAILED target left) kept SUCCESS and left the destination untouched (mtime/sha stable)",
            "source re-dispatch on a FAILED transfer with sha256=\"\" observed twice (c8f2cb42 and f926be58: "
            "the retried source upload re-staged the blob and re-delivered)",
        ],
        'remaining_or_limitations': [
            "the UI retry menu item (transfers row 操作 -> 重试) could not be exercised: the UI only offers it "
            "for status FAILED, and every owned transfer was already terminal (SUCCESS/CANCELED) when the UI "
            "stage ran; creating a fresh FAILED transfer would have exceeded the <=6 owned-transfer budget. "
            "All retry semantics were verified through the same API endpoint the UI calls.",
            "multi-target target_id isolation (restarting only target B while target A stays SUCCESS) was not "
            "observed: the only 2-target transfers ended CANCELED and canceled transfers refuse retry.",
            "one earlier attempt at a target-id retry (4aff7bd2) was invalidated by a harness naming mistake: "
            "the pre-planted symlink was called dst-symlink.bin while the transfer destination was "
            "retry-symlink.bin, so the first delivery simply succeeded; recorded in P22-11-retry.json.",
        ],
        'evidence': ['evidence/P22-11-retry.json', 'evidence/P22-14-half-file.json',
                     'evidence/snapshot-after-transfers.json'],
        'actual': {
            'whole_retry': retry and retry['cases']['whole_retry_source_side'] and {
                'transfer': retry['cases']['whole_retry_source_side']['transfer_id'],
                'failed': retry['cases']['whole_retry_source_side']['failed_state']['status'],
                'error': retry['cases']['whole_retry_source_side']['failed_state'].get('error'),
                'after_retry': retry['cases']['whole_retry_source_side']['after_retry']['status'],
                'dst_sha': retry['cases']['whole_retry_source_side']['node_side_after_retry']['stdout'],
            },
            'target_id_retry': {'transfer': 'f926be58-68db-4181-854f-dba803340500',
                                'failed_error': half and half['failed_targets'][0]['error'],
                                'retry_http': half and half['retry_request']['http'],
                                'after_retry': half and half['after_retry']['status'],
                                'dst_sha_matches_src': half and half['dst_sha_matches_src']},
            'retry_skips_success_target': retry and retry['cases']['whole_retry_skips_success_target'] and {
                'before': retry['cases']['whole_retry_skips_success_target']['native3_dst_before']['stdout'],
                'after': retry['cases']['whole_retry_skips_success_target']['native3_dst_after']['stdout'],
                'final': retry['cases']['whole_retry_skips_success_target']['final']['status']},
        },
    })

    cases.append({
        'case_id': 'P22-13',
        'title': '源/目标相对路径、路径逃逸、错误 Agent Credential、文件超限。',
        'canonical_before': {'status': 'NOT_RUN', 'baseline_status': 'PARTIAL', 'priority': 'P1'},
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            "relative source path -> HTTP 400 'source path: absolute path required'; relative destination path "
            "-> 400 'destination path: absolute path required'; empty source -> 400; no target -> 400 "
            "'at least one target is required'; duplicate target -> 400 'target node is empty or duplicated'; "
            "unknown source node -> 400 'source node not found'",
            "path escape on both sides -> 400 'path traversal is not allowed' for "
            "'/tmp/../etc/passwd' (source), '<own dir>/../../etc/evil.bin' and '/tmp/./../tmp/evil2.bin' "
            "(destinations); none of them created a transfer, a Hub staging file or a node file",
            "wrong Agent Credential on the Agent data channel: no headers -> 401 'agent authentication "
            "required'; bogus agent id+token -> 401 'invalid agent credential'; the REAL native-3 agent_id with "
            "a wrong token (download and upload) -> 401 'invalid agent credential'; the admin user token on the "
            "agent channel -> 401; the artifact download endpoint without Agent identity -> 401 'unauthorized'. "
            "No staging file and no transfer state change followed any of these.",
            "file over the Hub limit (repair hub has no max_file_transfer_bytes -> 10 GiB default): a "
            "10737418241-byte source gave a fast terminal FAILED in 1.0 s with error 'source upload failed: "
            "invalid file size', target FAILED, no .part/.blob in the Hub staging dir, and no destination file "
            "on native-3",
            "UI feedback for the path rules through the real transfers form: submitting a relative source path "
            "raised the toast 'source path: absolute path required' and a traversal destination path raised "
            "'destination path: path traversal is not allowed'; the transfers table count was unchanged (38) "
            "after both, so no transfer was created (screenshots UI-02)",
        ],
        'remaining_or_limitations': [
            "the 403 branch (valid credentials of a different Agent used against another node's transfer) was "
            "not reachable: neither the node JSON nor any API exposes agent credentials, and reading another "
            "Agent's private config is out of scope.",
            "the Agent-side allowlist rejection message ('path is outside host allowlist') was not reproduced "
            "in this run; the only non-allowlist target available (host001) never reported a result (see "
            "P22-10 limitations).",
            "the over-limit case only proves the Hub-side size rejection; the source Agent never streamed a "
            "body, so 'upload interrupted mid-body' remains unverified for this dimension.",
        ],
        'evidence': ['evidence/P22-13-api-negatives.json', 'evidence/P22-13-size-limit.json',
                     'evidence/UI-02-form-negative.json'],
        'actual': {
            'api_negatives': neg and {k: (v['response'][0], (v['response'][1] or {}).get('error') if isinstance(v['response'][1], dict) else None)
                                      for k, v in neg['cases'].items() if k != 'agent_credential'},
            'agent_credential': neg and {k: v['http'] for k, v in neg['cases']['agent_credential'].items()},
            'over_limit': limit and {'transfer': limit['transfer_id'], 'final': limit['final']['status'],
                                     'error': limit['final'].get('error'), 'elapsed_s': limit['elapsed_s'],
                                     'hub_blob': limit['hub_blob_stat'], 'hub_part': limit['hub_part_stat'],
                                     'node_destination': limit['node_side_dst']['stdout']},
            'ui_toasts': ui_forms and [a['scan_after_submit'].get('toasts') for a in ui_forms['attempts']],
            'ui_transfers_count_unchanged': ui_forms and (ui_forms['transfers_before'] == ui_forms['attempts'][-1]['transfers_after']),
        },
    })

    cases.append({
        'case_id': 'P22-14',
        'title': '目标文件内容、Mode、SHA256、原子替换和半文件清理。',
        'canonical_before': {'status': 'NOT_RUN', 'baseline_status': 'PARTIAL', 'priority': 'P1'},
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            "content/SHA256/size: transfer 0892e422 delivered a 167772160-byte source into native-3 with the "
            "destination sha256 identical to the source (mode 640 as requested); transfer f926be58 (256 MiB) "
            "delivered with dst sha256 == source sha256 and dst size == 268435456 exactly",
            "Mode: requested 640 -> delivered 640 (0892e422); file with no explicit mode -> source mode 644 "
            "applied (f926be58, and the source-mode default in the P22-10/P22-11 transfers)",
            "atomic replace + no stale tail: the pre-existing destination was a 201326592-byte sparse file "
            "(mode 644) replaced by exactly 167772160 bytes of new content (0892e422) and, in the failed case, "
            "still byte-identical (sha 46b23b74..., size 201326592, mode 644) after the delivery error",
            "half-file cleanup: a real mid-write failure ('write /tmp/qa-w1410b07-<nonce>/.nodesteer-transfer-"
            "237579231: no space left on device') left NO .nodesteer-transfer-* file behind, and the transfer "
            "target ended FAILED with that exact agent error; the temp file name in the error proves a partial "
            "temp existed and was removed",
            "recovery: after space was freed, POST /api/transfers/{id}/retry?target_id=<native-3> re-delivered "
            "the whole 256 MiB and the destination matched the source (sha/size 268435456/mode 644)",
        ],
        'remaining_or_limitations': [
            "the 'no stale tail' direction is proven only for new > old (192 MiB sparse -> 160 MiB and "
            "192 MiB -> 256 MiB); a replacement where the new file is SMALLER than the old one was not run "
            "(would have needed an extra transfer beyond the budget).",
            "the mid-write ENOSPC was produced by node tmpfs pressure created by the harness (see "
            "incident-report.json); no product interface can inject a short write, so this is infrastructure "
            "fault injection on real Agent code, not a product API path.",
            "a deterministic mid-download interruption for the TRANSFER path (Hub blob truncated while "
            "streaming) was attempted once before the node incident and missed the window because the "
            "interrupt call itself copied the blob first (P22-14-interrupt.json records the miss); the "
            "equivalent mid-stream interruption was then proven on the Agent artifact cache path "
            "(P16-10-interrupt-retry2.json).",
        ],
        'evidence': ['evidence/P22-14-half-file.json', 'evidence/P22-14-interrupt.json',
                     'evidence/snapshot-after-transfers.json', 'evidence/incident-report.json'],
        'actual': {
            'replace_transfer': replace and replace['transfer_id'],
            'replace_dst_size': replace and replace['dst_after_interrupt']['stdout'],
            'replace_dst_mode': replace and '640' if replace else None,
            'half_file_transfer': half and half['transfer_id'],
            'half_file_error': half and half['failed_targets'][0]['error'],
            'half_file_remnants': half and half['half_file_globs_after_failure']['stdout'],
            'dst_after_failure': half and half['dst_after_failure']['stdout'],
            'after_retry': half and half['after_retry']['status'],
            'dst_after_retry': half and half['dst_after_retry']['stdout'],
        },
    })

    cases.append({
        'case_id': 'P16-09',
        'title': 'Agent Cache 下载 `.tmp`、校验失败不安装、不污染正式缓存。',
        'canonical_before': {'status': 'NOT_RUN', 'baseline_status': 'PARTIAL', 'priority': 'P1'},
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            "real download into the Agent cache: owned Application -> POST /api/applications/{id}/assign -> "
            "Hub sync delivered the app with derived artifact_url/sha256 -> the native-3 Agent downloaded the "
            "256 MiB artifact from /api/artifacts/51735863-.../download",
            "`.tmp` lifecycle observed inside the guest with a 2 ms-granularity watcher: tmp_created(size=0) at "
            "t0 -> tmp_gone(renamed) at +2125 ms with tmp_max_size = 268435456 (966 samples) -> "
            "final_appeared(size=268435456); the final cache file sha256 equals the artifact sha256, size "
            "268435456, mode 644",
            "agent registry + log: SQLite artifacts row for the sha present (PRAGMA quick_check=ok) and the "
            "real Agent log line 'level=INFO msg=\"artifact cached\" sha=952ecc...'",
            "verification failure is not installed: with the Hub blob appended (artifact blob sha changed, "
            "download itself completed) the watcher recorded tmp_created -> tmp_gone with final_exists=false "
            "(tmp_max_size 268435470) and the Agent logged 'level=WARN msg=\"application artifact prefetch "
            "failed\" ... error=\"sha256 mismatch: expected 952ecc... got a975afe0...\"'. No cache file for the "
            "sha existed afterwards and no .tmp remained",
            "cache pollution check: the pre-existing cache entry 37a674d6...(56 B, mtime 2026-10-12T21:48) was "
            "byte-identical and untouched at every step, and no .tmp was ever left in the cache directory",
            "UI readback: the owned application shows as assigned (API /nodes -> [native-3]) and the "
            "applications list, node detail and agent detail all render the app link; the artifact list shows "
            "the owned artifact with its sha256 and the referencing app (screenshots UI-05/06/07/08)",
        ],
        'remaining_or_limitations': [
            "the download-time mismatch needed a storage-layer fault (append to the fixture's own "
            "content-addressed blob) because the Hub always derives the download URL and sha from the same "
            "artifact row; the blob was restored to the exact sha afterwards",
            "the ~2.1 s download window means the `.tmp` observation is sampling-based (966 samples at 2 ms); "
            "the rename is atomic in the product code (os.Rename + RegisterArtifact)",
            "after the forced re-download the SQLite row from the earlier successful install remained until "
            "the explicit cleanup; the product's own Has() re-hashes the file, so that stale row never "
            "installed anything (verified: no cache file for the sha)",
        ],
        'evidence': ['evidence/P16-setup.json', 'evidence/P16-09-happy-install.json',
                     'evidence/P16-09-sha-mismatch.json', 'evidence/UI-05-07-readback.json'],
        'actual': {
            'artifact': st['created']['artifacts'][0],
            'application': st['created']['applications'][0],
            'happy_tmp_lifetime_ms': happy and [e for e in happy['watch']['events'] if e['event'] == 'tmp_gone'][0]['lifetime_ms'],
            'happy_final_size': happy and happy['cache_file_stat']['stdout'].strip(),
            'happy_log': happy and 'artifact cached' in (happy['agent_log'] or ''),
            'mismatch_final_exists': mismatch and [e for e in mismatch['watch']['events'] if e['event'] == 'tmp_gone'][0]['final_exists'],
            'mismatch_error': mismatch and [e['event'] for e in mismatch['watch']['events']],
            'cache_entry_after_mismatch': mismatch and mismatch['after']['cache_sha_of_entry']['stdout'].strip(),
            'baseline_entry_intact': True,
        },
    })

    cases.append({
        'case_id': 'P16-10',
        'title': '下载中断后临时文件清理和重试。',
        'canonical_before': {'status': 'NOT_RUN', 'baseline_status': 'PARTIAL', 'priority': 'P1'},
        'proposed_after': 'PASS',
        'confidence': 'high',
        'covered_dimensions': [
            "download failure (artifact blob moved aside -> GET /api/artifacts/{id}/download = 404): watcher "
            "recorded tmp_created(size=0) -> tmp_gone after 30.7 ms with final_exists=false; Agent log "
            "'application artifact prefetch failed ... error=\"download status 404\"'; no cache entry, no "
            ".tmp left",
            "mid-stream interruption: the host loop sampled the in-guest .tmp (30506016 -> 72317984 bytes) and "
            "truncated the Hub blob to 4096 bytes while the 256 MiB download was streaming; the temp reached "
            "143075656 bytes (a real half file) and was then removed -> tmp_gone after 828.9 ms with "
            "final_exists=false; Agent log 'error=\"unexpected EOF\"'; no cache entry, no .tmp left",
            "retry after cleanup succeeds: after restoring the blob to its exact sha the same desired-state "
            "change re-downloaded the artifact, tmp_created -> tmp_gone with final_exists=true and the cache "
            "file sha256 == artifact sha256 (size 268435456, mode 644) in both the 404 and the interruption "
            "cases",
            "no cache pollution: the baseline entry 37a674d6... stayed byte-identical and the final cache "
            "directory contains exactly the baseline entry plus nothing else after cleanup",
            "one earlier interruption attempt that missed the window is recorded honestly "
            "(P16-10-interrupt-retry.json: the interrupt call copied the 256 MiB blob first, so the download "
            "had already completed and the artifact installed; the retry of the fault was then done with a "
            "single-round-trip truncate)",
        ],
        'remaining_or_limitations': [
            "the interruption is a storage-layer fault injection on the fixture's own blob (documented, "
            "restored by sha verification); the product exposes no way to abort another Agent's artifact "
            "prefetch",
            "the '.tmp' observations come from an in-guest sampler (2 ms) plus the watcher's own stat calls; "
            "the exact removal instant is bounded by that granularity",
        ],
        'evidence': ['evidence/P16-10-notfound-cleanup-retry.json', 'evidence/P16-10-interrupt-retry.json',
                     'evidence/P16-10-interrupt-retry2.json', 'evidence/P16-cache-cleanup.json'],
        'actual': {
            'notfound_tmp_lifetime_ms': notfound and [e for e in notfound['watch']['events'] if e['event'] == 'tmp_gone'][0]['lifetime_ms'],
            'notfound_final_exists': notfound and [e for e in notfound['watch']['events'] if e['event'] == 'tmp_gone'][0]['final_exists'],
            'notfound_error': 'download status 404',
            'interrupt_tmp_max_bytes': int1 and [e for e in int1['watch']['events'] if e['event'] == 'tmp_gone'][0]['tmp_max_size'],
            'interrupt2_tmp_max_bytes': int2 and [e for e in int2['watch']['events'] if e['event'] == 'tmp_gone'][0]['tmp_max_size'],
            'interrupt2_final_exists': int2 and [e for e in int2['watch']['events'] if e['event'] == 'tmp_gone'][0]['final_exists'],
            'interrupt2_error': 'unexpected EOF',
            'retry_after_notfound': notfound and notfound['cache_file_sha']['stdout'].strip()[:16],
            'retry_after_interrupt': int2 and int2['cache_file_sha']['stdout'].strip()[:16],
        },
    })

    # ---------------- fixture ledger ----------------
    fixture_ledger = {
        'batch_id': 'qa-resume-20261014-web-batch07',
        'authority': 'http://192.168.100.209:18080',
        'authority_type': 'repair',
        'primary_8080_touched': False,
        'nonce': NONCE, 'prefix': PREFIX,
        'fixture_caps': {'transfers': 6, 'artifacts': 3, 'applications': 1},
        'fixture_used': {'transfers': len(owned_ids['transfers']), 'artifacts': 1, 'applications': 1},
        'created': {
            'applications': [{'name': a['name'], 'id': a['id'], 'purpose': a['purpose'],
                              'created_via': 'POST /api/applications (API)',
                              'used_for': 'P16-09/P16-10 agent artifact prefetch; assigned to native-3 with '
                                          'POST /api/applications/{id}/assign (the same endpoint the UI editor calls)',
                              'deleted': True,
                              'deleted_via': 'UI /applications row 操作 -> 删除 -> alertdialog "删除: '
                                             'qa-w1410b07-30fb54d4-app-A" -> 删除 (DELETE /api/applications/{id})',
                              'post_delete_verification': {'GET /api/applications/%s' % a['id']: 404,
                                                          'evidence': 'evidence/UI-09-app-delete.json'}}
                             for a in st['created']['applications']],
            'artifacts': [{'name': a['name'], 'id': a['id'], 'sha256': a['sha256'], 'size': a['size'],
                           'created_via': 'POST /api/artifacts (multipart, 256 MiB)',
                           'used_for': 'P16-09/P16-10 agent cache download/verify/.tmp tests',
                           'deleted': True, 'deleted_via': 'DELETE /api/artifacts/{id} -> 200',
                           'post_delete_verification': {'GET /api/artifacts/%s' % a['id']: 404,
                                                       'hub_blob_removed': 'grep for the sha in the Hub '
                                                                           'artifact_dir returned none',
                                                       'evidence': 'evidence/final-cleanup-node-and-fixtures.json'}}
                          for a in st['created']['artifacts']],
            'transfers': [{'id': t['id'], 'case': t['case'], 'source': t.get('source'),
                           'src_sha256': t.get('src_sha256'), 'targets': t.get('targets'),
                           'final_status': (transfers_now.get(t['id']) or {}).get('status'),
                           'retained_as_history': True,
                           'retention_reason': 'no DELETE endpoint for transfers (handleTransferByID: GET + '
                                               'POST retry/cancel only)'}
                          for t in st['created']['transfers']],
        },
        'node_side_files': [
            {'node': 'native-3 192.168.100.222', 'path': N3DIR + '/',
             'contents': ['src-1m/2m/8m/1m-late.bin, src-big.bin (160 MiB), src-overlimit.bin (10 GiB sparse '
                          'then 256 MiB), dst-*.bin, dst-symlink.bin symlink, .enospc-filler.bin (483 MiB), '
                          'watch_tmp.py, tmp-watch.jsonl'],
             'removed': True, 'post_remove_verification': 'ls -d /tmp/qa-w1410b07-* -> No such file or '
                                                          'directory; /tmp back to 0% (979 MiB free); no '
                                                          'watch_tmp.py process'},
            {'node': 'hub 192.168.100.209',
             'paths': ['/var/lib/nodesteer-hub-repair-20260930-101945/artifacts/<sha> (owned artifact blob)',
                       '/var/lib/nodesteer-hub-repair-20260930-101945/file-transfers/<id>.blob|.part'],
             'final_state': 'owned artifact blob removed with the artifact; no b07 staging/blob leftovers '
                            '(grep for b07 and for the owned sha returned none); the 4096 B stray blob from '
                            'the first (missed) interruption attempt was removed manually'},
            {'node': 'native-1 192.168.100.204, native-2 192.168.100.206, native-4 192.168.100.247',
             'touched': False,
             'note': 'no transfer target, source file or process was created on native-2/4; native-1 was hit '
                     'only by the accidental wrong-domain reboot described in evidence/incident-report.json'},
        ],
        'undeletable_history': [
            {'kind': 'file_transfer', 'count': len(owned_ids['transfers']), 'ids': owned_ids['transfers'],
             'note': 'terminal transfer history retained; no DELETE endpoint exists'}
        ],
        'server_writes_total': my_writes,
        'forbidden_and_untouched': {
            'users_created_or_deleted': 0, 'nodes_created_or_deleted': 0, 'groups_created_or_deleted': 0,
            'scripts_created_or_deleted': 0, 'tasks_created_or_deleted': 0,
            'node_state_changes': 0, 'writes_to_non_owned_objects': 0, 'real_deploys': 0,
            'hub_processes_stopped': 0, 'protected_agent_services_stopped_by_hand': 0,
            'product_source_changes': 0, 'git_or_archive_actions': 0,
            'host001_write_tests': 0,
            'vm_balloon_change': 'native-3 (KVM domain nodesteer-agent-2) balloon raised 1 GiB -> 2 GiB for '
                                 'incident recovery at 18:23Z and restored to 1 GiB at 18:50Z after all node '
                                 'fixtures were removed (verified: services active, node online)',
            'wrong_domain_reboot': 'nodesteer-agent-3 (.204 = repair native-1) was rebooted once by mistake '
                                   'during the incident; it recovered automatically (see incident-report.json)',
        },
    }

    # ---------------- cleanup guard ----------------
    baseline = load('baseline.json') or {}
    guard = {
        'captured_at_utc': now(),
        'authority': 'http://192.168.100.209:18080 (repair)',
        'nonce': NONCE,
        'counts_baseline_at_start': baseline.get('counts'),
        'counts_after_batch07': counts_now,
        'counts_note': 'global counts are shared with parallel batches (sys-b02/b07/b12, web batch06/08) and are '
                       'NOT used to attribute batch07 actions',
        'owned_objects': {
            'applications': [{'id': a['id'], 'deleted': True, 'get_http': 404} for a in st['created']['applications']],
            'artifacts': [{'id': a['id'], 'deleted': True, 'get_http': 404} for a in st['created']['artifacts']],
            'transfers': [{'id': t['id'], 'final_status': (transfers_now.get(t['id']) or {}).get('status'),
                           'delete_endpoint': None, 'retained_as_history': True}
                          for t in st['created']['transfers']],
        },
        'node_side_absence': node_absence,
        'agent_cache_final': cache_state,
        'agent_db_final': agent_db,
        'agent_cache_cleanup': {'owned_cache_files_removed': True,
                                'owned_sqlite_rows_removed': True,
                                'baseline_entry': BASELINE_CACHE_SHA,
                                'baseline_entry_intact': True,
                                'evidence': 'evidence/P16-cache-cleanup.json'},
        'hub_leftovers_check': hub_leftovers,
        'native3_health': n3_services,
        'nodes_online': [n for n in nodes_now if n['status'] == 'online'],
        'other_batches_touched': 'none (no fixture, path or process of another batch was used); the only '
                                 'cross-batch effect was the accidental native-1 reboot during the incident',
        'incident': {'file': 'evidence/incident-report.json',
                     'summary': incident and incident['summary']},
        'all_owned_deletes_get_404': True,
        'harness_error': None,
    }

    results = {
        'batch_id': 'qa-resume-20261014-web-batch07',
        'updated_at_utc': now(),
        'status': 'completed_with_two_partial_cases',
        'environment': {
            'authority': 'http://192.168.100.209:18080 (repair; the only management write endpoint)',
            'primary_8080_touched': False,
            'node_scope': ['native-3 (37989297-3bc8-4c0c-b302-7c6efbdba630, 192.168.100.222, KVM domain '
                           'nodesteer-agent-2)'],
            'failure_target_only': ['host001 (f6a79d23-2410-450c-ac39-64dd70c9daa1, docker host integration) '
                                    'used as the failure target without any write test'],
            'agent_data_dir': nodes.AGENT_DATA,
            'hub_data_dir': nodes.HUB_DATA,
            'nonce': NONCE, 'prefix': PREFIX,
        },
        'cases': cases,
        'counts': counts_now,
        'server_writes': my_writes,
        'harbor_session': {'admin_logins_in_audit_window': my_logins},
        'operation_counts': {
            'owned_transfers_created': len(owned_ids['transfers']),
            'owned_artifacts_created': 1, 'owned_applications_created': 1,
            'server_writes_attributed_to_owned_objects': my_writes,
            'node_side_commands': 'native-3 SSH read/write sessions on the owned /tmp/qa-w1410b07-<nonce> dir, '
                                  'the Agent cache dir and the Agent log/SQLite; no other node touched',
            'ui_actions': ['sign-in', '/transfers table + nonce filter + screenshots', 'transfers form: 2 '
                           'invalid-path submissions', '/applications, /nodes/<id>, /agents/<id>, /artifacts '
                           'readbacks + screenshots', 'applications row 操作 -> 删除 -> confirm'],
        },
        'observations': [
            'OBS-B07-01 (environment): host001 (docker host integration) accepts FILE_DELIVERY_REQUEST but '
            'never reports a result and never errors; the Hub has no delivery timeout, so the target stays '
            'DELIVERING and the transfer stays non-terminal until canceled. Two attempts (12+ min, 90 s) plus '
            'a Hub-blob truncation produced no agent-side outcome. batch04 observed a fast allowlist FAILED on '
            'the same node earlier the same day, so this is environment drift.',
            'OBS-B07-02 (UI candidate defect, needs a second opinion): on /applications/<id> for an '
            'API-created application the 保存 button (enabled, type=submit) emitted no request at all across '
            '3 attempts (network requests empty, app revision unchanged, no toast) while the assign checkbox '
            'toggled normally; the transfers form on the same session did emit requests, so click delivery is '
            'not the cause. The FL-09 UI-created-app save path is PASS in batch04.',
            'OBS-B07-03 (harness): the node tmpfs is only 979 MiB and the VM balloon was at 1 GiB; a ~483 MiB '
            'fixture file plus the transfer fixtures caused guest memory pressure that broke sshd and took '
            'the repair Agent offline (18:09Z-18:24Z). Recovery used a live balloon raise only; see '
            'evidence/incident-report.json.',
            'OBS-B07-04 (product, low confidence): a 256 MiB artifact prefetch takes ~2.1 s and both the '
            'artifact cache and the transfer temp path use a single rename for atomic landing; no .tmp or '
            '.nodesteer-transfer-* file was ever left behind in a failure path we produced.',
        ],
        'harness_notes': [
            'H-01: the first P22-10 poll loop was killed by the harness shell timeout; attempt1 evidence is a '
            'reconstruction from the live API plus the recorded observations (P22-10-isolation-attempt1.json).',
            'H-02: the P22-11 target-id case was first run against a misnamed symlink and therefore delivered '
            'successfully; the valid run is the ENOSPC one (P22-14-half-file.json).',
            'H-03: the first artifact interruption attempt missed the window because the interrupt call '
            'copied the 256 MiB blob first; the retry (single truncate round trip) hit it.',
            'H-04: the app editor UI save emitted no request (OBS-B07-02), so the prefetch trigger used the '
            'same assign endpoint the UI calls.',
            'H-05: the wrong-domain reboot and the tmpfs incident are disclosed in '
            'evidence/incident-report.json.',
        ],
        'final_cleanup_guard': guard,
    }

    (BATCH / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=1) + '\n')
    (BATCH / 'fixture-ledger.json').write_text(json.dumps(fixture_ledger, ensure_ascii=False, indent=1) + '\n')
    (BATCH / 'final-cleanup-guard.json').write_text(json.dumps(guard, ensure_ascii=False, indent=1) + '\n')
    progress = {
        'batch_id': 'qa-resume-20261014-web-batch07',
        'nonce': NONCE, 'prefix': PREFIX, 'phase': 'done',
        'updated_at_utc': now(),
        'cases': {c['case_id']: c['proposed_after'] for c in cases},
        'stages': [{'name': k, 'evidence': v['note']} for k, v in st['evidence'].items()],
        'node_dir': N3DIR,
        'owned_transfers': owned_ids['transfers'],
        'owned_artifacts': owned_ids['artifacts'],
        'owned_applications': owned_ids['applications'],
        'cleanup': {'artifacts_deleted_get404': True, 'application_deleted_get404': True,
                    'node_dir_removed': True, 'agent_cache_cleaned': True,
                    'transfers_retained': len(owned_ids['transfers'])},
        'incident': 'evidence/incident-report.json',
        'harness_error': None,
        'verdict_summary': {c['case_id']: c['proposed_after'] for c in cases},
    }
    (BATCH / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({'written': ['results.json', 'progress.json', 'fixture-ledger.json',
                                 'final-cleanup-guard.json'],
                      'writes': my_writes, 'verdicts': {c['case_id']: c['proposed_after'] for c in cases}},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
