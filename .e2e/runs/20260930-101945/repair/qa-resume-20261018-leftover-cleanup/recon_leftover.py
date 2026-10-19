#!/usr/bin/env python3
"""qa-resume-20261018-leftover-cleanup: read-only recon of the 4 authorized targets.

Targets (all confirmed-owned per housekeeping/artcache-cleanup.json):
  T1 native-2 (192.168.100.206): container+volume+dir nonce f174890c3846 + hub node record
  T2 hub-209 (192.168.100.209): /var/lib/nodesteer-hub-repair-20260930-101945-ttl-ui + related files
  T3 native-1 (192.168.100.204): unit qa-20260930-101945-web-app-6a32f.service + dir + hub app record
  T4 hub-209: docker images qa-20260930-101945-env-{agent,hub}:dddee4f0

Pure read-only: no stop/delete/write on hosts; hub side only GET.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
EV.mkdir(exist_ok=True, parents=True)
os.umask(0o077)
sys.path.insert(0, str(EV / 'tools'))
import hubclient  # noqa: E402

KEY = str(Path.home() / '.ssh' / 'id_ed25519')
N2 = '192.168.100.206'
N1 = '192.168.100.204'
HUB = '192.168.100.209'
NONCE = 'f174890c3846'
APP_UNIT = 'qa-20260930-101945-web-app-6a32f.service'
APP_DIR = '/tmp/qa-repair-20260930-101945-app-web'
TTL_DIR = '/var/lib/nodesteer-hub-repair-20260930-101945-ttl-ui'
TTL_UNIT = 'nodesteer-hub-repair-20260930-101945-ttl-ui.service'
TTL_CFG = '/etc/nodesteer/nodesteer-hub-repair-20260930-101945-ttl-ui.yaml'
TTL_BAK = '/etc/nodesteer/hub-repair-20260930-101945.yaml.pre-ttl-test'
NODE_ID = 'f6a79d23-2410-450c-ac39-64dd70c9daa1'
IMG_A = 'qa-20260930-101945-env-agent:dddee4f0'
IMG_H = 'qa-20260930-101945-env-hub:dddee4f0'


def ssh(host, script, timeout=120):
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
        return {'error': 'parse_%s' % type(exc).__name__,
                'stdout_tail': cp.stdout[-800:], 'stderr_tail': cp.stderr[-400:]}


# ---------------- T1: native-2 docker host fixture ----------------
N2_SCRIPT = r'''
import json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def run(cmd, t=30):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return r.stdout.strip()
C = 'qa-resume-20261010-host-f174890c3846-host001'
V = 'qa-resume-20261010-host-f174890c3846-data-1'
D = '/opt/qa-resume-20261010-host-f174890c3846'
out['docker_ps_all'] = run("docker ps -a --format '{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}'").splitlines()
out['container_inspect'] = run("docker inspect %s 2>/dev/null" % C)
out['volume_ls'] = run("docker volume ls --format '{{.Name}}|{{.Driver}}'").splitlines()
out['volume_inspect'] = run("docker volume inspect %s 2>/dev/null" % V)
out['container_mounts'] = run("docker inspect -f '{{json .Mounts}}' %s 2>/dev/null" % C)
out['container_labels'] = run("docker inspect -f '{{json .Config.Labels}}' %s 2>/dev/null" % C)
out['container_state'] = run("docker inspect -f '{{json .State}}' %s 2>/dev/null" % C)
out['dir_exists'] = os.path.isdir(D)
out['dir_islink'] = os.path.islink(D)
out['dir_listing'] = run("ls -la %s 2>/dev/null" % D).splitlines()
out['dir_find'] = run("find %s -maxdepth 4 -printf '%%y %%s %%p\\n' 2>/dev/null | head -200" % D).splitlines()
out['dir_du'] = run("du -sh %s 2>/dev/null" % D).strip()
out['mounts_grep'] = run("mount | grep -i f174890c3846 || true").splitlines()
out['procs_grep'] = run("ps -eo pid,args --no-headers | grep -i f174890c3846 | grep -v grep || true").splitlines()
out['units_grep'] = run("grep -rl f174890c3846 /etc/systemd/system 2>/dev/null || true").splitlines()
out['units_list'] = run("systemctl list-units --all --no-pager --no-legend 2>/dev/null | grep -Ei 'qa|nodesteer' || true").splitlines()
out['docker_version'] = run("docker version --format '{{.Server.Version}}' 2>/dev/null || echo NO_DOCKER")
print(json.dumps(out))
'''

# ---------------- T2 + T4: hub-209 ttl-ui residue + docker images ----------------
HUB_SCRIPT = r'''
import hashlib, json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def run(cmd, t=40):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return r.stdout.strip()
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
# -- unit state
out['unit_show'] = run("systemctl show --no-pager -p LoadState,ActiveState,SubState,UnitFileState,MainPID,FragmentPath,InvocationID '%s' 2>&1" % U).splitlines()
out['unit_enabled'] = run("systemctl is-enabled '%s' 2>&1 || true" % U)
out['unit_active'] = run("systemctl is-active '%s' 2>&1 || true" % U)
out['unit_file_exists'] = os.path.exists('/etc/systemd/system/' + U)
out['unit_file_listing'] = run("ls -la /etc/systemd/system/%s 2>/dev/null || true" % U).splitlines()
out['unit_file_sha'] = sha('/etc/systemd/system/' + U)
out['unit_wants_links'] = run("ls -la /etc/systemd/system/multi-user.target.wants/ 2>/dev/null | grep -i ttl || true").splitlines()
out['unit_show_all_props'] = run("systemctl show --no-pager -p Names,Description,ExecStart,Environment '%s' 2>&1" % U).splitlines()
# -- port / process / fd
out['port_18995'] = run("ss -tlnp 2>/dev/null | grep 18995 || true").splitlines()
out['port_18995_udp'] = run("ss -ulnp 2>/dev/null | grep 18995 || true").splitlines()
out['procs_grep'] = run("ps -eo pid,user,args --no-headers | grep -i ttl-ui | grep -v grep || true").splitlines()
out['fd_refs'] = run("for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -q 'ttl-ui' && echo ${p#/proc/}; done | head -20")
out['cwd_refs'] = run("for p in /proc/[0-9]*; do readlink $p/cwd 2>/dev/null | grep -q 'ttl-ui' && echo ${p#/proc/}; done | head -20")
# -- data dir inventory
out['dir_exists'] = os.path.isdir(D)
out['dir_listing'] = run("ls -la %s 2>/dev/null" % D).splitlines()
inv = []
if os.path.isdir(D):
    for root, dnames, fnames in os.walk(D):
        for n in sorted(fnames):
            fp = os.path.join(root, n)
            st = os.lstat(fp)
            inv.append({'path': fp, 'size': st.st_size, 'mode': oct(st.st_mode & 0o777),
                        'uid': st.st_uid, 'mtime': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(st.st_mtime)),
                        'sha256': sha(fp)})
    for root, dnames, fnames in os.walk(D):
        for dn in sorted(dnames):
            dp = os.path.join(root, dn)
            st = os.lstat(dp)
            inv.append({'path': dp + '/', 'dir': True, 'mode': oct(st.st_mode & 0o777), 'uid': st.st_uid})
out['dir_inventory'] = inv
out['dir_du'] = run("du -sh %s 2>/dev/null" % D).strip()
# -- related config files
out['cfg_exists'] = os.path.exists(CFG)
out['cfg_stat'] = run("stat -c '%%U:%%G %%a %%s %%y' %s 2>/dev/null || true" % CFG).strip()
out['cfg_sha'] = sha(CFG)
out['cfg_head_keys'] = run("sed -n '1,60p' %s 2>/dev/null | sed -E 's/(password|token):.*/\\1: [REDACTED]/'" % CFG).splitlines()
out['bak_exists'] = os.path.exists(BAK)
out['bak_stat'] = run("stat -c '%%U:%%G %%a %%s %%y' %s 2>/dev/null || true" % BAK).strip()
out['bak_sha'] = sha(BAK)
out['bak_head_keys'] = run("sed -n '1,60p' %s 2>/dev/null | sed -E 's/(password|token):.*/\\1: [REDACTED]/'" % BAK).splitlines()
out['etc_nodesteer_listing'] = run("ls -la /etc/nodesteer/ 2>/dev/null").splitlines()
# -- grep for other references to ttl-ui
out['grep_etc_systemd'] = run("grep -rl 'ttl-ui' /etc/systemd/system /etc/nodesteer 2>/dev/null || true").splitlines()
out['main_repair_hub_state'] = run("systemctl show --no-pager -p ActiveState,MainPID nodesteer-hub-repair-20260930-101945.service").splitlines()
# -- docker images (T4)
out['docker_ps_all'] = run("docker ps -a --format '{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}'").splitlines()
out['docker_images'] = run("docker images --format '{{.Repository}}:{{.Tag}}|{{.ID}}|{{.CreatedAt}}|{{.Size}}' | grep -E 'qa-20260930-101945-env' || true").splitlines()
out['docker_image_inspect_agent'] = run("docker image inspect qa-20260930-101945-env-agent:dddee4f0 2>/dev/null")
out['docker_image_inspect_hub'] = run("docker image inspect qa-20260930-101945-env-hub:dddee4f0 2>/dev/null")
out['containers_using_imgs'] = run("docker ps -a --filter ancestor=qa-20260930-101945-env-agent:dddee4f0 --format '{{.Names}}' ; docker ps -a --filter ancestor=qa-20260930-101945-env-hub:dddee4f0 --format '{{.Names}}'").splitlines()
print(json.dumps(out))
'''

# ---------------- T3: native-1 app fixture ----------------
N1_SCRIPT = r'''
import json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def run(cmd, t=30):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return r.stdout.strip()
U = 'qa-20260930-101945-web-app-6a32f.service'
D = '/tmp/qa-repair-20260930-101945-app-web'
out['unit_show'] = run("systemctl show --no-pager -p LoadState,ActiveState,SubState,UnitFileState,MainPID,FragmentPath '%s' 2>&1" % U).splitlines()
out['unit_enabled'] = run("systemctl is-enabled '%s' 2>&1 || true" % U)
out['unit_active'] = run("systemctl is-active '%s' 2>&1 || true" % U)
out['unit_status'] = run("systemctl status --no-pager -n 5 '%s' 2>&1 | head -25" % U).splitlines()
out['unit_file_exists'] = os.path.exists('/etc/systemd/system/' + U)
out['unit_file_listing'] = run("ls -la /etc/systemd/system/%s 2>/dev/null || true" % U).splitlines()
out['unit_file_content'] = run("cat /etc/systemd/system/%s 2>/dev/null" % U).splitlines()
out['unit_wants_links'] = run("ls -la /etc/systemd/system/multi-user.target.wants/ 2>/dev/null | grep 6a32f || true").splitlines()
out['cgroup_procs'] = run("systemctl show --no-pager -p MainPID,ControlPID '%s' 2>&1" % U).splitlines()
mpid = run("systemctl show -p MainPID --value '%s' 2>/dev/null" % U)
out['mainpid'] = mpid
if mpid and mpid != '0':
    out['mainpid_exe'] = run("readlink /proc/%s/exe 2>/dev/null; cat /proc/%s/cmdline 2>/dev/null | tr '\\0' ' '; echo" % (mpid, mpid)).splitlines()
out['procs_grep'] = run("ps -eo pid,user,args --no-headers | grep -E '6a32f|qa-repair-20260930-101945-app-web' | grep -v grep || true").splitlines()
out['dir_exists'] = os.path.isdir(D)
out['dir_islink'] = os.path.islink(D)
out['dir_listing'] = run("ls -la %s 2>/dev/null" % D).splitlines()
out['dir_find'] = run("find %s -maxdepth 5 -printf '%%y %%s %%p\\n' 2>/dev/null | head -200" % D).splitlines()
out['dir_du'] = run("du -sh %s 2>/dev/null" % D).strip()
out['fd_refs'] = run("for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -q 'qa-repair-20260930-101945-app-web' && echo ${p#/proc/}; done | head -20")
out['other_units_6a32f'] = run("ls /etc/systemd/system/ 2>/dev/null | grep 6a32f || true").splitlines()
out['units_list'] = run("systemctl list-units --all --no-pager --no-legend 2>/dev/null | grep -Ei 'qa-2026|nodesteer' || true").splitlines()
print(json.dumps(out))
'''


def main():
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'targets': {}}
    # ---------- hub API recon ----------
    hub = hubclient.Hub()
    hub.login()
    h = {}
    code, body = hub.get('/api/nodes')
    rows = body.get('nodes', body) if isinstance(body, dict) else body
    h['nodes_all'] = [{'id': n.get('id'), 'hostname': n.get('hostname'), 'status': n.get('status'),
                       'sync_status': n.get('sync_status'), 'agent_version': n.get('agent_version'),
                       'labels': n.get('labels')} for n in rows]
    h['target_node'] = next((n for n in h['nodes_all'] if n['id'] == NODE_ID), None)
    code, body = hub.get('/api/nodes/' + NODE_ID)
    h['target_node_get_http'] = code
    h['target_node_get_body'] = body
    code, body = hub.get('/api/applications')
    apps = body if isinstance(body, list) else (body.get('applications') if isinstance(body, dict) else [])
    h['applications_all'] = [{'id': a.get('id'), 'name': a.get('name'), 'revision': a.get('revision')} for a in apps]
    app = next((a for a in apps if (a.get('name') or '').startswith('qa-repair-20260930-101945')), None)
    h['target_app'] = {'id': app.get('id'), 'name': app.get('name'), 'revision': app.get('revision')} if app else None
    if app:
        code, body = hub.get('/api/applications/%s/nodes' % app['id'])
        h['target_app_nodes_http'] = code
        h['target_app_nodes'] = body
        code, body = hub.get('/api/applications/%s/state' % app['id'])
        h['target_app_state_http'] = code
        h['target_app_state'] = body
        code, body = hub.get('/api/applications/%s' % app['id'])
        h['target_app_get_http'] = code
    code, transfers = hub.get('/api/transfers')
    tr = transfers if isinstance(transfers, list) else []
    h['transfers_total'] = len(tr)
    h['transfers_nonterminal'] = [{'id': t.get('id'), 'status': t.get('status'),
                                  'source_path': t.get('source_path'),
                                  'targets': [{'node_id': x.get('node_id'), 'status': x.get('status')} for x in (t.get('targets') or [])]}
                                 for t in tr if t.get('status') not in ('SUCCESS', 'FAILED', 'CANCELED')]
    h['transfers_referencing_target_node'] = [{'id': t.get('id'), 'status': t.get('status'),
                                              'targets': [{'node_id': x.get('node_id'), 'status': x.get('status')} for x in (t.get('targets') or [])]}
                                             for t in tr if any((x.get('node_id') == NODE_ID) for x in (t.get('targets') or []))]
    code, execs = hub.get('/api/executions?node_id=' + NODE_ID)
    ex = execs if isinstance(execs, list) else (execs.get('executions') if isinstance(execs, dict) else [])
    h['execs_target_node_total'] = len(ex)
    h['execs_target_node_active'] = [{'id': e.get('id'), 'status': e.get('status'), 'task_id': e.get('task_id')}
                                     for e in ex if e.get('status') in ('PENDING', 'RUNNING')]
    out['targets']['hub_api'] = h
    # ---------- host recon ----------
    out['targets']['native-2'] = ssh(N2, N2_SCRIPT)
    out['targets']['hub-209'] = ssh(HUB, HUB_SCRIPT)
    out['targets']['native-1'] = ssh(N1, N1_SCRIPT)
    (EV / 'recon-raw.json').write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
    # console summary (no secrets)
    print(json.dumps({
        'hub_node_target': (h['target_node'] or {}).get('hostname'),
        'hub_app_target': (h['target_app'] or {}).get('name'),
        'hub_transfers_nonterminal': len(h['transfers_nonterminal']),
        'hub_execs_active_on_node': len(h['execs_target_node_active']),
        'n2_errors': out['targets']['native-2'].get('error'),
        'hub209_errors': out['targets']['hub-209'].get('error'),
        'n1_errors': out['targets']['native-1'].get('error'),
    }, indent=1))


if __name__ == '__main__':
    main()
