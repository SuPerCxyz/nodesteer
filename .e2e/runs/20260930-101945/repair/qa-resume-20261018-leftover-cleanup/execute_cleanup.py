#!/usr/bin/env python3
"""qa-resume-20261018-leftover-cleanup: execute the 4 authorized cleanups.

CAS-style: every target re-verifies its exact pre-delete inventory (name/sha)
on the host right before deletion; on mismatch the target is skipped and
reported. Destructive actions are limited to the 4 authorized items.
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
NODE_HOST = 'qa-resume-20261010-host-f174890c3846-host001'
APP_ID = '804e6b14-eb84-4dda-90de-8bcab9586c4c'
APP_NODE = '91f44866-8a28-435e-bbda-2f2367126c00'

# expected pre-delete hashes (from evidence/predelete-inventory.json)
H_TTL_UNIT = 'f4deef495be166665c39e3e8f771f59a3e5d3cfbaa880383fa900fb77127317a'
H_TTL_DB = '3a1c98fae81eddaeb12640c1c563ee65fb992ae5574214f7836adb5f84037c6b'
H_TTL_CFG = '2c8fe647cbc93745f8fe80748792535b4dfc0bb3276b962a05a5e801c4835c4d'
H_TTL_BAK = 'e594591f164b67d4974352c3b29eaa86eb8b67498c4f1af127d6c0c8131f6ade'
H_APP_UNIT = '8af129462ea804939c8ddf0a71658022b8107acc5a01f4973a594e8b02d8a36c'
H_APP_BIN = '9dbe15d4319d863093eade25419f25a68ca6d1f56aa805022b8f13dd3710e6ed'


def ssh(host, script, timeout=260):
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
        return {'error': 'parse_%s' % type(exc).__name__, 'stdout_tail': cp.stdout[-800:],
                'stderr_tail': cp.stderr[-400:]}


T1_SCRIPT = r'''
import json, os, shutil, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'steps': {}}
def run(cmd, t=90):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return {'rc': r.returncode, 'out': (r.stdout + r.stderr).strip()[-400:]}
C = 'qa-resume-20261010-host-f174890c3846-host001'
V = 'qa-resume-20261010-host-f174890c3846-data-1'
D = '/opt/qa-resume-20261010-host-f174890c3846'
# -- CAS guards
guard = {}
guard['container_present'] = C in run("docker ps -a --format '{{.Names}}'")['out'].splitlines()
guard['container_owner'] = run("docker inspect -f '{{index .Config.Labels \"nodesteer.e2e.owner\"}}' %s" % C)['out'] == 'qa-resume-20261010-host-f174890c3846'
guard['volume_present'] = V in run("docker volume ls --format '{{.Name}}'")['out'].splitlines()
owner_ok = False
try:
    with open(D + '/.owner.json') as f:
        o = json.load(f)
    owner_ok = (o.get('nonce') == 'f174890c3846' and o.get('run') == 'qa-resume-20261010-host-f174890c3846')
except Exception:
    pass
guard['dir_owner_ok'] = owner_ok
out['guards'] = guard
if not all(guard.values()):
    out['aborted'] = True
    print(json.dumps(out)); raise SystemExit
# -- stop container
out['steps']['stop'] = run("docker stop -t 10 %s" % C)
out['steps']['stop_state'] = run("docker inspect -f '{{.State.Running}}' %s 2>/dev/null || echo gone" % C)
# -- remove container
out['steps']['rm'] = run("docker rm %s" % C)
out['steps']['rm_check'] = run("docker ps -a --format '{{.Names}}' | grep -c '^%s$' || true" % C)
# -- remove volume
out['steps']['volume_rm'] = run("docker volume rm %s" % V)
out['steps']['volume_check'] = run("docker volume ls --format '{{.Name}}' | grep -c '^%s$' || true" % V)
# -- remove dir
try:
    shutil.rmtree(D)
    out['steps']['dir_rm'] = {'rc': 0, 'out': 'rmtree done'}
except Exception as exc:
    out['steps']['dir_rm'] = {'rc': 1, 'out': type(exc).__name__}
out['steps']['dir_check'] = {'exists': os.path.exists(D)}
print(json.dumps(out))
'''

T2_SCRIPT = r'''
import hashlib, json, os, shutil, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'steps': {}}
def run(cmd, t=90):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return {'rc': r.returncode, 'out': (r.stdout + r.stderr).strip()[-500:]}
def sha(p):
    try:
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for c in iter(lambda: f.read(1 << 20), b''):
                h.update(c)
        return h.hexdigest()
    except Exception as e:
        return 'ERR:' + type(e).__name__
U = 'nodesteer-hub-repair-20260930-101945-ttl-ui.service'
D = '/var/lib/nodesteer-hub-repair-20260930-101945-ttl-ui'
CFG = '/etc/nodesteer/nodesteer-hub-repair-20260930-101945-ttl-ui.yaml'
BAK = '/etc/nodesteer/hub-repair-20260930-101945.yaml.pre-ttl-test'
UP = '/etc/systemd/system/' + U
# -- CAS guards
guard = {}
guard['unit_file_sha_ok'] = sha(UP) == 'f4deef495be166665c39e3e8f771f59a3e5d3cfbaa880383fa900fb77127317a'
guard['unit_disabled'] = run("systemctl is-enabled '%s' 2>&1 || true" % U)['out'] == 'disabled'
guard['unit_inactive'] = run("systemctl is-active '%s' 2>&1 || true" % U)['out'] == 'inactive'
guard['port_free'] = run("ss -ltn 2>/dev/null | grep -c 18995 || true")['out'].startswith('0')
guard['db_sha_ok'] = sha(D + '/hub.db') == '3a1c98fae81eddaeb12640c1c563ee65fb992ae5574214f7836adb5f84037c6b'
guard['cfg_sha_ok'] = sha(CFG) == '2c8fe647cbc93745f8fe80748792535b4dfc0bb3276b962a05a5e801c4835c4d'
guard['bak_sha_ok'] = sha(BAK) == 'e594591f164b67d4974352c3b29eaa86eb8b67498c4f1af127d6c0c8131f6ade'
out['guards'] = guard
if not all(guard.values()):
    out['aborted'] = True
    print(json.dumps(out)); raise SystemExit
# -- disable --now (idempotent) + delete unit
out['steps']['disable_now'] = run("systemctl disable --now '%s' 2>&1 || true" % U, 150)
out['steps']['rm_unit'] = run("rm -f '%s' && echo removed" % UP)
out['steps']['daemon_reload'] = run("systemctl daemon-reload && echo reloaded")
out['steps']['unit_check'] = run("systemctl show --no-pager -p LoadState,ActiveState,UnitFileState '%s' 2>&1" % U)
# -- remove config + backup + data dir
out['steps']['rm_cfg'] = run("rm -f '%s' && echo removed" % CFG)
out['steps']['rm_bak'] = run("rm -f '%s' && echo removed" % BAK)
try:
    shutil.rmtree(D)
    out['steps']['dir_rm'] = {'rc': 0, 'out': 'rmtree done'}
except Exception as exc:
    out['steps']['dir_rm'] = {'rc': 1, 'out': type(exc).__name__}
out['steps']['checks'] = {
    'unit_file_exists': os.path.exists(UP),
    'cfg_exists': os.path.exists(CFG),
    'bak_exists': os.path.exists(BAK),
    'dir_exists': os.path.exists(D),
    'port_free': run("ss -ltn 2>/dev/null | grep -c 18995 || true")['out'].startswith('0'),
}
print(json.dumps(out))
'''

T4_SCRIPT = r'''
import json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'steps': {}}
def run(cmd, t=90):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return {'rc': r.returncode, 'out': (r.stdout + r.stderr).strip()[-500:]}
A = 'qa-20260930-101945-env-agent:dddee4f0'
H = 'qa-20260930-101945-env-hub:dddee4f0'
# -- CAS guards
ps = run("docker ps -a --format '{{.ID}}|{{.Names}}|{{.Image}}'")['out']
guard = {
    'no_containers_at_all': ps == '',
    'agent_img_present': run("docker image inspect -f '{{.Id}}' %s 2>/dev/null" % A)['out'].startswith('sha256:'),
    'hub_img_present': run("docker image inspect -f '{{.Id}}' %s 2>/dev/null" % H)['out'].startswith('sha256:'),
}
out['guards'] = guard
if not all(guard.values()):
    out['aborted'] = True
    print(json.dumps(out)); raise SystemExit
out['steps']['rmi'] = run("docker rmi %s %s" % (A, H))
out['steps']['check'] = run("docker images --format '{{.Repository}}:{{.Tag}}' | grep -c 'qa-20260930-101945-env' || true")
print(json.dumps(out))
'''

T3_SCRIPT = r'''
import hashlib, json, os, shutil, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'steps': {}}
def run(cmd, t=90):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return {'rc': r.returncode, 'out': (r.stdout + r.stderr).strip()[-500:]}
def sha(p):
    try:
        h = hashlib.sha256()
        with open(p, 'rb') as f:
            for c in iter(lambda: f.read(1 << 20), b''):
                h.update(c)
        return h.hexdigest()
    except Exception as e:
        return 'ERR:' + type(e).__name__
U = 'qa-20260930-101945-web-app-6a32f.service'
D = '/tmp/qa-repair-20260930-101945-app-web'
UP = '/etc/systemd/system/' + U
BIN = D + '/app-6a32f/binary'
# -- CAS guards
guard = {
    'unit_file_sha_ok': sha(UP) == '8af129462ea804939c8ddf0a71658022b8107acc5a01f4973a594e8b02d8a36c',
    'bin_sha_ok': sha(BIN) == '9dbe15d4319d863093eade25419f25a68ca6d1f56aa805022b8f13dd3710e6ed',
    'unit_active': run("systemctl is-active '%s' 2>&1 || true" % U)['out'] == 'active',
}
out['guards'] = guard
if not all(guard.values()):
    out['aborted'] = True
    print(json.dumps(out)); raise SystemExit
# -- disable --now (stops + removes wants symlink)
out['steps']['disable_now'] = run("systemctl disable --now '%s' 2>&1 || true" % U, 150)
out['steps']['state_after_stop'] = run("systemctl show --no-pager -p ActiveState,SubState,UnitFileState,MainPID '%s'" % U)
out['steps']['wants_link_check'] = run("test -L /etc/systemd/system/multi-user.target.wants/%s && echo LINK || echo NO_LINK" % U)
# -- delete unit file
out['steps']['rm_unit'] = run("rm -f '%s' && echo removed" % UP)
out['steps']['daemon_reload'] = run("systemctl daemon-reload && echo reloaded")
out['steps']['unit_check'] = run("systemctl show --no-pager -p LoadState,ActiveState,UnitFileState '%s' 2>&1" % U)
# -- delete dir
try:
    shutil.rmtree(D)
    out['steps']['dir_rm'] = {'rc': 0, 'out': 'rmtree done'}
except Exception as exc:
    out['steps']['dir_rm'] = {'rc': 1, 'out': type(exc).__name__}
out['steps']['checks'] = {
    'unit_file_exists': os.path.exists(UP),
    'dir_exists': os.path.exists(D),
    'procs_grep': run("ps -eo pid,args --no-headers | grep '6a32f' | grep -v grep || true")['out'],
}
print(json.dumps(out))
'''


def main():
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'targets': {}, 'hub': {}}
    hub = hubclient.Hub()
    hub.login()
    # ================= T1 =================
    out['targets']['T1_native-2'] = ssh(N2, T1_SCRIPT)
    # hub node delete (product path), guarded by live terminal-state recheck
    t1 = {}
    code, tr = hub.get('/api/transfers')
    nonterminal = [t.get('id') for t in tr if t.get('status') not in ('SUCCESS', 'FAILED', 'CANCELED')]
    code, ex = hub.get('/api/executions?node_id=' + NODE_ID)
    active = [e.get('id') for e in ex if e.get('status') in ('PENDING', 'RUNNING')]
    t1['precheck_nonterminal_transfers'] = nonterminal
    t1['precheck_active_executions'] = active
    code, node = hub.get('/api/nodes/' + NODE_ID)
    t1['node_get_before'] = code
    if code == 200 and not nonterminal and not active:
        c, b = hub.delete('/api/nodes/' + NODE_ID)
        t1['node_delete_http'] = c
        t1['node_delete_body'] = b
        time.sleep(1.0)
        c, b = hub.get('/api/nodes/' + NODE_ID)
        t1['node_get_after'] = c
        t1['node_get_after_body'] = b
    else:
        t1['node_delete_skipped'] = 'precheck not clean'
    out['hub']['T1_node'] = t1
    # ================= T2 =================
    out['targets']['T2_hub-209'] = ssh(HUB, T2_SCRIPT)
    # ================= T4 =================
    out['targets']['T4_hub-209'] = ssh(HUB, T4_SCRIPT)
    # ================= T3 =================
    out['targets']['T3_native-1'] = ssh(N1, T3_SCRIPT)
    t3 = {}
    code, app = hub.get('/api/applications/' + APP_ID)
    t3['app_get_before'] = code
    if code == 200:
        c, b = hub.post('/api/applications/%s/assign' % APP_ID,
                        {'node_ids': [APP_NODE], 'remove': True})
        t3['assign_remove_http'] = c
        t3['assign_remove_body'] = b
        c, nodes = hub.get('/api/applications/%s/nodes' % APP_ID)
        t3['app_nodes_after_remove'] = nodes
        c, b = hub.delete('/api/applications/' + APP_ID)
        t3['app_delete_http'] = c
        t3['app_delete_body'] = b
        time.sleep(1.0)
        c, b = hub.get('/api/applications/' + APP_ID)
        t3['app_get_after'] = c
        t3['app_get_after_body'] = b
    else:
        t3['app_delete_skipped'] = 'app not found before cleanup'
    out['hub']['T3_app'] = t3
    (EV / 'exec-raw.json').write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
    # ---- summary into cleanup-exec.json ----
    def ok(v):
        return 'error' not in v
    summary = {'at_utc': out['at_utc'], 'results': {}}
    t1r = out['targets']['T1_native-2']
    summary['results']['T1'] = {
        'host_ok': ok(t1r), 'aborted': t1r.get('aborted', False),
        'container_removed': (t1r.get('steps', {}).get('rm_check', {}).get('out') == '0'),
        'volume_removed': (t1r.get('steps', {}).get('volume_check', {}).get('out') == '0'),
        'dir_removed': (t1r.get('steps', {}).get('dir_check', {}).get('exists') is False),
        'hub_node_delete_http': t1.get('node_delete_http'),
        'hub_node_get_after': t1.get('node_get_after'),
    }
    t2r = out['targets']['T2_hub-209']
    summary['results']['T2'] = {
        'host_ok': ok(t2r), 'aborted': t2r.get('aborted', False),
        'checks_after': t2r.get('steps', {}).get('checks'),
        'unit_check': t2r.get('steps', {}).get('unit_check', {}).get('out'),
    }
    t4r = out['targets']['T4_hub-209']
    summary['results']['T4'] = {
        'host_ok': ok(t4r), 'aborted': t4r.get('aborted', False),
        'images_left': t4r.get('steps', {}).get('check', {}).get('out'),
    }
    t3r = out['targets']['T3_native-1']
    summary['results']['T3'] = {
        'host_ok': ok(t3r), 'aborted': t3r.get('aborted', False),
        'checks_after': t3r.get('steps', {}).get('checks'),
        'unit_check': t3r.get('steps', {}).get('unit_check', {}).get('out'),
        'assign_remove_http': t3.get('assign_remove_http'),
        'app_nodes_after_remove': t3.get('app_nodes_after_remove'),
        'app_delete_http': t3.get('app_delete_http'),
        'app_get_after': t3.get('app_get_after'),
    }
    (HERE / 'cleanup-exec.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps(summary['results'], ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
