#!/usr/bin/env python3
"""qa-resume-20261018-leftover-cleanup: post-cleanup rescan + oracle.

Independent rescan of all 4 targets after cleanup, plus protected-state checks
(:8080 primary hub, nodesteer-agent.service units, repair nodes, other-batch
fixtures untouched).
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
os.umask(0o077)
sys.path.insert(0, str(EV / 'tools'))
import hubclient  # noqa: E402

KEY = str(Path.home() / '.ssh' / 'id_ed25519')
N2, N1, HUB = '192.168.100.206', '192.168.100.204', '192.168.100.209'
NODE_ID = 'f6a79d23-2410-450c-ac39-64dd70c9daa1'
APP_ID = '804e6b14-eb84-4dda-90de-8bcab9586c4c'


def ssh(host, script, timeout=180):
    cp = subprocess.run(
        ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
         '-o', 'ConnectTimeout=8', '-i', KEY, 'root@' + host, 'python3', '-'],
        input=script, capture_output=True, text=True, timeout=timeout)
    lines = [l for l in cp.stdout.splitlines() if l.strip()]
    if cp.returncode != 0 and not lines:
        return {'error': 'ssh_rc_%d' % cp.returncode, 'stderr': cp.stderr[-500:]}
    try:
        return json.loads(lines[-1])
    except Exception as exc:
        return {'error': 'parse_%s' % type(exc).__name__, 'stdout_tail': cp.stdout[-800:]}


RESCAN_N2 = r'''
import json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def run(cmd, t=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return r.stdout.strip()
C = 'qa-resume-20261010-host-f174890c3846-host001'
V = 'qa-resume-20261010-host-f174890c3846-data-1'
D = '/opt/qa-resume-20261010-host-f174890c3846'
out['container_absent'] = C not in run("docker ps -a --format '{{.Names}}'").splitlines()
out['docker_ps_all'] = run("docker ps -a --format '{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}'").splitlines()
out['volume_absent'] = V not in run("docker volume ls --format '{{.Name}}'").splitlines()
out['docker_volumes'] = run("docker volume ls --format '{{.Name}}'").splitlines()
out['dir_absent'] = not os.path.exists(D)
out['find_nonce'] = run("find /opt /var/lib /var/tmp /root -maxdepth 3 -name '*f174890c3846*' 2>/dev/null | head -20").splitlines()
out['find_vol_dir'] = run("ls -d /var/lib/docker/volumes/*f174890c3846* 2>/dev/null || echo NONE")
out['retained_image_present'] = 'nodesteer-qa/agent-host:f174890c3846' in run("docker images --format '{{.Repository}}:{{.Tag}}'").splitlines()
out['docker_images'] = run("docker images --format '{{.Repository}}:{{.Tag}}'").splitlines()
out['primary_agent_state'] = run("systemctl show --no-pager -p ActiveState,SubState,MainPID,UnitFileState nodesteer-agent.service").splitlines()
out['repair_agent_state'] = run("systemctl show --no-pager -p ActiveState,SubState,MainPID,UnitFileState nodesteer-agent-repair-20260930-101945-native-2.service").splitlines()
out['units_list_qa'] = run("systemctl list-units --all --no-pager --no-legend 2>/dev/null | grep -Ei 'qa-2026|f174890c3846' || true").splitlines()
print(json.dumps(out))
'''

RESCAN_HUB = r'''
import json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def run(cmd, t=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return r.stdout.strip()
U = 'nodesteer-hub-repair-20260930-101945-ttl-ui.service'
D = '/var/lib/nodesteer-hub-repair-20260930-101945-ttl-ui'
CFG = '/etc/nodesteer/nodesteer-hub-repair-20260930-101945-ttl-ui.yaml'
BAK = '/etc/nodesteer/hub-repair-20260930-101945.yaml.pre-ttl-test'
# T2 rescan
out['unit_state'] = run("systemctl show --no-pager -p LoadState,ActiveState,UnitFileState '%s' 2>&1" % U).splitlines()
out['unit_file_absent'] = not os.path.exists('/etc/systemd/system/' + U)
out['unit_files_grep'] = run("systemctl list-unit-files --no-pager --no-legend 2>/dev/null | grep 'ttl-ui' || true").splitlines()
out['dir_absent'] = not os.path.exists(D)
out['cfg_absent'] = not os.path.exists(CFG)
out['bak_absent'] = not os.path.exists(BAK)
out['live_cfg_present'] = os.path.exists('/etc/nodesteer/hub-repair-20260930-101945.yaml')
out['port_free'] = run("ss -ltn 2>/dev/null | grep -c 18995 || true").startswith('0')
out['find_ttl'] = run("find /var/lib /etc/nodesteer -maxdepth 2 -name '*ttl-ui*' 2>/dev/null | head -10").splitlines()
# T4 rescan
out['env_images_absent'] = run("docker images --format '{{.Repository}}:{{.Tag}}' | grep -c 'qa-20260930-101945-env' || true")
out['docker_images'] = run("docker images --format '{{.Repository}}:{{.Tag}}'").splitlines()
out['docker_ps_a'] = run("docker ps -a --format '{{.ID}}|{{.Names}}|{{.Image}}'").splitlines()
# protected state
out['repair_hub_state'] = run("systemctl show --no-pager -p ActiveState,SubState,MainPID,UnitFileState nodesteer-hub-repair-20260930-101945.service").splitlines()
out['repair_hub_health_18080'] = run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/healthz")
out['primary_hub_state'] = run("systemctl show --no-pager -p ActiveState,SubState,MainPID,UnitFileState nodesteer-hub.service").splitlines()
out['primary_hub_health_8080'] = run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/healthz")
print(json.dumps(out))
'''

RESCAN_N1 = r'''
import json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def run(cmd, t=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return r.stdout.strip()
U = 'qa-20260930-101945-web-app-6a32f.service'
D = '/tmp/qa-repair-20260930-101945-app-web'
out['unit_state'] = run("systemctl show --no-pager -p LoadState,ActiveState,UnitFileState '%s' 2>&1" % U).splitlines()
out['unit_file_absent'] = not os.path.exists('/etc/systemd/system/' + U)
out['wants_link_absent'] = not os.path.exists('/etc/systemd/system/multi-user.target.wants/' + U)
out['dir_absent'] = not os.path.exists(D)
out['procs_grep'] = run("ps -eo pid,args --no-headers | grep '6a32f' | grep -v grep || true").splitlines()
out['find_6a32f'] = run("find /etc/systemd /tmp -maxdepth 3 -name '*6a32f*' 2>/dev/null | head -10").splitlines()
out['tmp_listing'] = run("ls -la /tmp/ | head -20").splitlines()
out['agent_state_db_refs_note'] = run("grep -c '6a32f' /var/lib/nodesteer-repair-20260930-101945/native-1/state.db 2>/dev/null || true")
out['primary_agent_state'] = run("systemctl show --no-pager -p ActiveState,SubState,MainPID,UnitFileState nodesteer-agent.service").splitlines()
out['repair_agent_state'] = run("systemctl show --no-pager -p ActiveState,SubState,MainPID,UnitFileState nodesteer-agent-repair-20260930-101945-native-1.service").splitlines()
print(json.dumps(out))
'''


def main():
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'targets': {}, 'hub': {}}
    out['targets']['native-2'] = ssh(N2, RESCAN_N2)
    out['targets']['hub-209'] = ssh(HUB, RESCAN_HUB)
    out['targets']['native-1'] = ssh(N1, RESCAN_N1)
    hub = hubclient.Hub()
    hub.login()
    h = {}
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    h['nodes_all'] = [{'id': n.get('id'), 'hostname': n.get('hostname'), 'status': n.get('status'),
                       'sync_status': n.get('sync_status'), 'agent_version': n.get('agent_version')} for n in rows]
    code, body = hub.get('/api/nodes/' + NODE_ID)
    h['target_node_get_after'] = code
    h['target_node_absent'] = (code == 404)
    code, body = hub.get('/api/applications/' + APP_ID)
    h['target_app_get_after'] = code
    h['target_app_absent'] = (code == 404)
    code, apps = hub.get('/api/applications')
    apps = apps if isinstance(apps, list) else []
    h['apps_after'] = [{'id': a.get('id'), 'name': a.get('name')} for a in apps]
    code, tr = hub.get('/api/transfers')
    h['transfers_nonterminal_after'] = [t.get('id') for t in tr if t.get('status') not in ('SUCCESS', 'FAILED', 'CANCELED')]
    code, ex = hub.get('/api/executions?node_id=' + NODE_ID)
    h['execs_target_node_after'] = len(ex)
    repair = [n for n in h['nodes_all'] if (n.get('hostname') or '').startswith('qa-repair-20260930-101945')]
    h['repair_nodes'] = repair
    h['repair_nodes_online_synced'] = len(repair) == 4 and all(
        n['status'] == 'online' and n['sync_status'] == 'synced' for n in repair)
    h['other_batch_node_still_present'] = any(
        (n.get('hostname') or '').startswith('qa-resume-20261008-native4-child') for n in h['nodes_all'])
    out['hub'] = h
    (EV / 'rescan-raw.json').write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
    # ---------------- oracle verdict ----------------
    n2 = out['targets']['native-2']
    hb = out['targets']['hub-209']
    n1 = out['targets']['native-1']
    verdict = {
        'T1_container_absent': n2.get('container_absent'),
        'T1_volume_absent': n2.get('volume_absent'),
        'T1_dir_absent': n2.get('dir_absent'),
        'T1_find_nonce_empty': n2.get('find_nonce') == [],
        'T1_hub_node_404': h.get('target_node_absent'),
        'T1_retained_image_still_present': n2.get('retained_image_present'),
        'T2_unit_not_found': 'LoadState=not-found' in (hb.get('unit_state') or []),
        'T2_unit_file_absent': hb.get('unit_file_absent'),
        'T2_dir_absent': hb.get('dir_absent'),
        'T2_cfg_absent': hb.get('cfg_absent'),
        'T2_bak_absent': hb.get('bak_absent'),
        'T2_live_cfg_present': hb.get('live_cfg_present'),
        'T2_port_free': hb.get('port_free'),
        'T3_unit_not_found': 'LoadState=not-found' in (n1.get('unit_state') or []),
        'T3_unit_file_absent': n1.get('unit_file_absent'),
        'T3_dir_absent': n1.get('dir_absent'),
        'T3_no_procs': n1.get('procs_grep') == [],
        'T3_hub_app_404': h.get('target_app_absent'),
        'T4_env_images_absent': hb.get('env_images_absent') == '0',
        'protected_repair_hub_active': 'ActiveState=active' in (hb.get('repair_hub_state') or []),
        'protected_repair_hub_healthy': hb.get('repair_hub_health_18080') == '200',
        'protected_primary_hub_active': 'ActiveState=active' in (hb.get('primary_hub_state') or []),
        'protected_primary_hub_healthy': hb.get('primary_hub_health_8080') == '200',
        'protected_repair_nodes_online_synced': h.get('repair_nodes_online_synced'),
        'protected_n1_primary_agent_active': 'ActiveState=active' in (n1.get('primary_agent_state') or []),
        'protected_n2_primary_agent_active': 'ActiveState=active' in (n2.get('primary_agent_state') or []),
        'other_batch_node_untouched': h.get('other_batch_node_still_present'),
        'no_nonterminal_transfers_after': h.get('transfers_nonterminal_after') == [],
    }
    oracle = {
        'task': 'qa-resume-20261018-leftover-cleanup post-cleanup oracle',
        'at_utc': out['at_utc'],
        'verdict': verdict,
        'all_pass': all(v is True for v in verdict.values()),
        'targets': out['targets'],
        'hub': out['hub'],
        'notes': [
            'T1 retained image nodesteer-qa/agent-host:f174890c3846 stays on native-2 (not in authorized item list).',
            'T2 live repair-hub config /etc/nodesteer/hub-repair-20260930-101945.yaml untouched; the .pre-ttl-test copy was byte-identical and removed.',
            'T3 native-1 repair agent state.db still holds agent-side rows referencing the deleted app (out of authorized scope, report-only).',
            'Retained out-of-scope items re-confirmed: 20261008 child node, app artifact, qa-web-b05 scripts, nodesteer/agent repair images.',
        ],
    }
    (HERE / 'post-cleanup-oracle.json').write_text(json.dumps(oracle, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps(verdict, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
