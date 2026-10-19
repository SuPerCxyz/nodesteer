#!/usr/bin/env python3
"""qa-resume-20261018-leftover-cleanup: pre-delete inventory (read-only, CAS baseline).

Captures exact hashes/inventories of the 4 authorized targets so the delete step
can re-verify (compare-and-swap style) before removing anything.
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
N2, N1, HUB = '192.168.100.206', '192.168.100.204', '192.168.100.209'
NODE_ID = 'f6a79d23-2410-450c-ac39-64dd70c9daa1'
APP_ID = '804e6b14-eb84-4dda-90de-8bcab9586c4c'


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
        return {'error': 'parse_%s' % type(exc).__name__, 'stdout_tail': cp.stdout[-600:]}


T1 = r'''
import hashlib, json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def run(cmd, t=60):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=t)
    return r.stdout.strip()
def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()
C = 'qa-resume-20261010-host-f174890c3846-host001'
V = 'qa-resume-20261010-host-f174890c3846-data-1'
D = '/opt/qa-resume-20261010-host-f174890c3846'
out['container_name_exact'] = C in run("docker ps -a --format '{{.Names}}'").splitlines()
out['container_id'] = run("docker inspect -f '{{.Id}}' %s 2>/dev/null" % C)
out['container_running'] = run("docker inspect -f '{{.State.Running}}' %s 2>/dev/null" % C)
out['container_image'] = run("docker inspect -f '{{.Config.Image}}' %s 2>/dev/null" % C)
out['container_labels'] = json.loads(run("docker inspect -f '{{json .Config.Labels}}' %s 2>/dev/null" % C) or '{}')
out['container_mounts'] = json.loads(run("docker inspect -f '{{json .Mounts}}' %s 2>/dev/null" % C) or '[]')
out['volume_inspect'] = json.loads(run("docker volume inspect %s 2>/dev/null" % V) or '[]')
out['volume_data_files'] = run("find /var/lib/docker/volumes/%s/_data -maxdepth 3 -printf '%%y %%s %%p\\n' 2>/dev/null | head -100" % V).splitlines()
out['owner_json'] = run("cat %s/.owner.json 2>/dev/null" % D)
inv = []
if os.path.isdir(D) and not os.path.islink(D):
    for root, dnames, fnames in os.walk(D):
        for n in sorted(fnames):
            fp = os.path.join(root, n)
            st = os.lstat(fp)
            inv.append({'path': fp, 'size': st.st_size, 'mode': oct(st.st_mode & 0o777), 'sha256': sha(fp)})
out['dir_inventory'] = inv
out['dir_du'] = run("du -sh %s 2>/dev/null" % D).strip()
out['dir_top_listing'] = run("ls -la %s 2>/dev/null" % D).splitlines()
out['private_agent_yaml_sha'] = sha(D + '/private/host001-agent.yaml')
out['docker_ps_before'] = run("docker ps -a --format '{{.ID}}|{{.Names}}|{{.Status}}|{{.Image}}'").splitlines()
print(json.dumps(out))
'''

T2T4 = r'''
import hashlib, json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def run(cmd, t=60):
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
CUR = '/etc/nodesteer/hub-repair-20260930-101945.yaml'
# T2
out['unit_show'] = run("systemctl show --no-pager -p LoadState,ActiveState,SubState,UnitFileState,MainPID '%s' 2>&1" % U).splitlines()
out['unit_file_sha'] = sha('/etc/systemd/system/' + U)
out['unit_file_content'] = run("cat /etc/systemd/system/%s 2>/dev/null" % U).splitlines()
out['port_free_tcp'] = not bool(run("ss -ltn 2>/dev/null | grep 18995"))
out['port_free_udp'] = not bool(run("ss -lun 2>/dev/null | grep 18995"))
inv = []
for root, dnames, fnames in os.walk(D):
    for n in sorted(fnames):
        fp = os.path.join(root, n)
        st = os.lstat(fp)
        inv.append({'path': fp, 'size': st.st_size, 'mode': oct(st.st_mode & 0o777), 'sha256': sha(fp)})
    for dn in sorted(dnames):
        dp = os.path.join(root, dn)
        st = os.lstat(dp)
        inv.append({'path': dp + '/', 'dir': True, 'mode': oct(st.st_mode & 0o777)})
out['dir_inventory'] = inv
out['cfg_sha'] = sha(CFG)
out['bak_sha'] = sha(BAK)
out['cur_sha'] = sha(CUR)
out['bak_equals_cur'] = (out['bak_sha'] == out['cur_sha'])
out['fd_refs'] = run("for p in /proc/[0-9]*; do ls -l $p/fd 2>/dev/null | grep -q 'ttl-ui' && echo ${p#/proc/}; done | head -20")
out['procs_grep'] = run("ps -eo pid,args --no-headers | grep -i 'ttl-ui' | grep -v grep || true").splitlines()
# T4
out['img_agent_id'] = run("docker image inspect -f '{{.Id}}' qa-20260930-101945-env-agent:dddee4f0 2>/dev/null")
out['img_hub_id'] = run("docker image inspect -f '{{.Id}}' qa-20260930-101945-env-hub:dddee4f0 2>/dev/null")
out['docker_ps_a'] = run("docker ps -a --format '{{.ID}}|{{.Names}}|{{.Image}}'").splitlines()
out['repair_hub_state'] = run("systemctl show --no-pager -p ActiveState,MainPID nodesteer-hub-repair-20260930-101945.service").splitlines()
out['repair_hub_health'] = run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/healthz")
print(json.dumps(out))
'''

T3 = r'''
import hashlib, json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def run(cmd, t=60):
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
U = 'qa-20260930-101945-web-app-6a32f.service'
D = '/tmp/qa-repair-20260930-101945-app-web'
out['unit_show'] = run("systemctl show --no-pager -p LoadState,ActiveState,SubState,UnitFileState,MainPID,FragmentPath '%s' 2>&1" % U).splitlines()
out['unit_file_sha'] = sha('/etc/systemd/system/' + U)
out['wants_link_target'] = run("readlink /etc/systemd/system/multi-user.target.wants/%s 2>/dev/null || echo NO_LINK" % U)
inv = []
for root, dnames, fnames in os.walk(D):
    for n in sorted(fnames):
        fp = os.path.join(root, n)
        st = os.lstat(fp)
        inv.append({'path': fp, 'size': st.st_size, 'mode': oct(st.st_mode & 0o777), 'sha256': sha(fp)})
out['dir_inventory'] = inv
out['mainpid'] = run("systemctl show -p MainPID --value '%s'" % U)
out['procs_grep'] = run("ps -eo pid,args --no-headers | grep '6a32f' | grep -v grep || true").splitlines()
print(json.dumps(out))
'''


def main():
    out = {'at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    out['native-2'] = ssh(N2, T1)
    out['hub-209'] = ssh(HUB, T2T4)
    out['native-1'] = ssh(N1, T3)
    # hub-side baseline
    hub = hubclient.Hub()
    hub.login()
    code, node = hub.get('/api/nodes/' + NODE_ID)
    out['hub_node_get_http'] = code
    out['hub_node'] = node
    code, app = hub.get('/api/applications/' + APP_ID)
    out['hub_app_get_http'] = code
    out['hub_app'] = app
    code, nodes = hub.get('/api/applications/%s/nodes' % APP_ID)
    out['hub_app_nodes'] = nodes
    (EV / 'predelete-inventory.json').write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({
        'n2': out['native-2'].get('error'),
        'hub209': out['hub-209'].get('error'),
        'n1': out['native-1'].get('error'),
        'hub_node_get_http': code,
    }, indent=1))


if __name__ == '__main__':
    main()
