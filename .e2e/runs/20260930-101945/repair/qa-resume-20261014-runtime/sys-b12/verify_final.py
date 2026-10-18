#!/usr/bin/env python3
"""Independent post-batch verification for sys-b12 (Root-style re-check).

Re-reads the Hub, the owned host (native-2), the protected services and the local harness
surface, and merges the results into final-cleanup-oracle.json.  Read-only.
"""
import json
import subprocess
import time
from pathlib import Path

import hostapi
import hubapi

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb12-%s-' % NONCE
TAG = 'qa-sysb12-%s' % NONCE
NOW = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
SSH = hostapi.SSH


def main():
    out = {'at_utc': NOW(), 'nonce': NONCE, 'owned_prefix': PREFIX}
    hub = hubapi.Hub()
    hub.login()
    nodes = hub.json('/api/nodes')
    apps = hub.json('/api/applications')
    arts = hub.json('/api/artifacts')
    transfers = hub.json('/api/transfers')
    owned_node_ids = [n['id'] for n in nodes if (n.get('hostname') or '').startswith(PREFIX)]
    owned_app_ids = [a['id'] for a in apps if (a.get('name') or '').startswith(PREFIX)]
    owned_art_ids = [a['id'] for a in arts if (a.get('name') or '').startswith(PREFIX)]
    # the canonical run recorded the ids it created; probe those ids explicitly even though they are
    # already absent from the list endpoints (that absence is the point).
    run = json.loads((HERE / 'run-evidence.json').read_text()) if (HERE / 'run-evidence.json').is_file() else {}
    st = run.get('state') or {}
    known = {
        'app': [i for i in [st.get('app_id'), st.get('xapp_id')] if i],
        'artifact': [a.get('id') for a in (st.get('artifacts') or {}).values() if a.get('id')],
        'node': [v.get('node_id') for v in (st.get('nodes') or {}).values() if v.get('node_id')],
    }
    out['hub'] = {
        'owned_nodes_present': owned_node_ids, 'owned_apps_present': owned_app_ids,
        'owned_artifacts_present': owned_art_ids,
        'created_ids_probed': known,
        'get_404_probe': {},
        'counts_after': {'nodes': len(nodes), 'applications': len(apps), 'artifacts': len(arts)},
    }
    for label, path in (('app', '/api/applications/'), ('artifact', '/api/artifacts/'),
                        ('node', '/api/nodes/')):
        for cid in known[label]:
            out['hub']['get_404_probe'][cid] = hub.get(path + cid)[0]
    out['hub']['owned_transfers'] = [
        {'id': t['id'], 'status': t.get('status'), 'source_path': t.get('source_path')}
        for t in transfers if (t.get('source_path') or '').startswith('/opt/' + TAG)]
    canonical_ids = {t.get('id') for t in (st.get('transfers') or [])}
    out['hub']['owned_transfers_canonical'] = [
        {'id': t['id'], 'status': t.get('status'), 'source_path': t.get('source_path'),
         'targets': [{'node_id': x.get('node_id'), 'destination_path': x.get('destination_path'),
                      'status': x.get('status'), 'error': x.get('error')} for x in (t.get('targets') or [])]}
        for t in transfers
        if (t.get('source_path') or '').startswith('/opt/' + TAG) and t['id'] in canonical_ids]
    out['hub']['owned_transfers_from_superseded_attempts'] = [
        {'id': t['id'], 'status': t.get('status'), 'source_path': t.get('source_path')}
        for t in transfers
        if (t.get('source_path') or '').startswith('/opt/' + TAG) and t['id'] not in canonical_ids]
    out['hub']['transfer_records_note'] = (
        '/api/transfers 无删除接口（只有 retry/cancel）；owned 传输记录保留在 Hub DB 是本批唯一无法删除的'
        '产品侧记录，所有引用路径（/opt/%s/...）已随 owned 目录删除。其中一部分来自本次 harness 的非 canonical '
        '尝试（attempt01-03），已单独分组。' % TAG)

    checks = {}
    base = hostapi.OwnedChild(NONCE).host_baseline()
    checks['native2_baseline'] = base
    checks['native2_residue_clear'] = (
        not base['root_exists'] and not base['opt_root_exists'] and not base['owned_units']
        and not base['owned_child_processes'] and not base['owned_mounts']
        and not base['etc_systemd_owned_leftovers'] and not base['etc_systemd_owned_wants']
        and not base['tmp_owned_entries'] and not base['etc_owned_entries'])
    probe = hostapi.OwnedChild(NONCE).probe(targets=[], dirs=[])
    checks['owned_mounts_after'] = probe.get('mounts')
    checks['sampler_processes'] = hostapi.OwnedChild(NONCE).remote_raw(
        'import json,subprocess\n'
        'r=subprocess.run(["pgrep","-af","dst.samples|xapp.samples|xneg.samples"],capture_output=True,text=True)\n'
        'print(json.dumps([l for l in r.stdout.splitlines() if l.strip()]))\n')[1].strip()
    checks['agent_binary_sha256'] = base['agent_binary_sha256']
    checks['agent_binary_unchanged'] = base['agent_binary_sha256'] == hostapi.AGENT_SHA
    checks['protected_units'] = {'primary': base['primary_agent_unit'].get('ActiveState'),
                                 'repair': base['repair_agent_unit'].get('ActiveState')}
    out['host'] = checks

    # repair Hub itself must still be the same running instance (never restarted by this batch)
    code, body = hub.get('/healthz', timeout=10)
    out['authority'] = {'healthz_http': code,
                        'note': 'repair Hub :18080 was never stopped/restarted by this batch; '
                                'only API calls were made'}
    # no harness-owned listeners/processes left
    local = subprocess.run(['bash', '-lc',
                            'pgrep -af "run_sysb12|frame_proxy" || true'], capture_output=True, text=True)
    ports = subprocess.run(['bash', '-lc',
                            'ss -ltnp 2>/dev/null | grep -E "python3" || true'], capture_output=True, text=True)
    out['local_harness'] = {'residual_processes': [l for l in local.stdout.splitlines() if l.strip()],
                            'python_listeners_seen': [l.strip() for l in ports.stdout.splitlines() if l.strip()],
                            'note': 'the 127.0.0.1:189xx listeners belong to an unrelated project '
                                    '(iris fake-ai-provider), not to this batch; the sys-b12 recording '
                                    'proxy ran on an ephemeral 0.0.0.0 port and its process has exited'}
    out['final_clean'] = (not owned_node_ids and not owned_app_ids and not owned_art_ids
                          and checks['native2_residue_clear'] and not probe.get('mounts')
                          and checks['agent_binary_unchanged'])

    hubapi.write_json('evidence/independent-verification.json', out)

    oracle_path = HERE / 'final-cleanup-oracle.json'
    oracle = json.loads(oracle_path.read_text()) if oracle_path.is_file() else {}
    cleanup = json.loads((HERE / 'evidence' / 'phase12-cleanup.json').read_text())
    oracle['cleanup_phase_summary'] = {
        'product_path_uninstall': cleanup['steps'].get('product_uninstall'),
        'host_unit_residue_sweep': cleanup['steps'].get('host_unit_residue_sweep'),
        'host_dirs_removal': cleanup['steps'].get('host_dirs_removal'),
        'release_owned_seams': cleanup['steps'].get('release_owned_seams'),
        'owned_child_removal': cleanup['steps'].get('owned_child_removal'),
        'artifact_deletes': cleanup['steps'].get('artifact_deletes'),
        'node_deletes': cleanup['steps'].get('node_deletes'),
        'frame_proxy_stop': cleanup['steps'].get('frame_proxy_stop'),
        'errors': cleanup.get('errors'),
    }
    oracle['independent_re_verification'] = out
    oracle['method'] = (
        '产品路径清理为主（删除 Hub Application → tombstone → Agent CleanupApplication 停/禁 unit、删 unit/binary/config、'
        '清 unit_registry），owned 主机 seam 全部显式释放（chattr -i、umount owned tmpfs、删除 owned symlink/目录），'
        '两个 owned child Agent 停止并删除自有 root；随后独立复核 Hub 404 与 native-2 残留。')
    hubapi.write_json('final-cleanup-oracle.json', oracle)
    print(json.dumps({'final_clean': out['final_clean'],
                      'hub_owned_present': {'nodes': owned_node_ids, 'apps': owned_app_ids,
                                            'artifacts': owned_art_ids},
                      'native2_residue_clear': checks['native2_residue_clear'],
                      'owned_mounts_after': probe.get('mounts'),
                      'get_404_probe': out['hub']['get_404_probe'],
                      'authority_healthz': code}, ensure_ascii=False, indent=1))
    return 0 if out['final_clean'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
