#!/usr/bin/env python3
"""qa-resume-20261018-housekeeping: read-only host scan (task1 + task4 base data).

Scans hub host + 4 native nodes for QA leftovers (qa-resume/qa-final prefixes,
artifact-cache/recovery-script residue), boot times (no-reboot evidence) and
port/process state.  Pure read-only: no write, no stop, no delete on hosts.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
EV.mkdir(exist_ok=True, parents=True)
os.umask(0o077)

KEY = str(Path.home() / '.ssh' / 'id_ed25519')
HOSTS = {
    'hub-209': '192.168.100.209',
    'native-1': '192.168.100.204',
    'native-2': '192.168.100.206',
    'native-3': '192.168.100.222',
    'native-4': '192.168.100.247',
}

REMOTE = r'''
import json, os, subprocess, time
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
def ls(p):
    try:
        return sorted(os.listdir(p))
    except Exception as e:
        return ['ERR:' + type(e).__name__]
def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return r.stdout
out['opt'] = ls('/opt')
out['var_lib'] = ls('/var/lib')
out['etc_systemd'] = ls('/etc/systemd/system')
out['var_tmp'] = ls('/var/tmp')
out['tmp'] = ls('/tmp')
out['root'] = ls('/root')
out['find_qa'] = run("find /opt /var/lib /var/tmp /root /srv /data -maxdepth 3 \\( -iname '*qa-*' -o -iname '*qa_*' \\) 2>/dev/null | head -300").splitlines()
out['find_nodesteer'] = run("find /opt /var/lib /var/tmp /root -maxdepth 3 -iname '*nodesteer*' 2>/dev/null | head -400").splitlines()
out['procs'] = run("ps -eo pid,user,args --no-headers | grep -E 'qa-|nodesteer' | grep -v grep").splitlines()
out['ports'] = run("ss -tlnp 2>/dev/null").splitlines()
out['docker_ps'] = run("command -v docker >/dev/null 2>&1 && timeout 10 docker ps -a --format '{{.ID}} {{.Names}} {{.Status}} {{.Image}}' 2>/dev/null || echo NO_DOCKER").splitlines()
out['units'] = run("systemctl list-units --all --no-pager --no-legend 2>/dev/null | grep -E 'qa|nodesteer'").splitlines()
out['uptime_s'] = run("uptime -s").strip()
out['proc_uptime'] = run("cat /proc/uptime").strip()
out['repair_dir'] = ls('/opt/nodesteer-repair-20260930-101945')
print(json.dumps(out))
'''


def main():
    results = {}
    for name, ip in HOSTS.items():
        try:
            cp = subprocess.run(
                ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
                 '-o', 'ConnectTimeout=8', '-i', KEY, 'root@' + ip, 'python3', '-'],
                input=REMOTE, capture_output=True, text=True, timeout=150)
        except subprocess.TimeoutExpired:
            results[name] = {'error': 'ssh_timeout'}
            continue
        if cp.returncode != 0:
            results[name] = {'error': 'ssh_rc_%d' % cp.returncode, 'stderr': cp.stderr[-500:]}
            continue
        lines = [l for l in cp.stdout.splitlines() if l.strip()]
        try:
            results[name] = json.loads(lines[-1])
        except Exception as exc:
            results[name] = {'error': 'parse_%s' % type(exc).__name__, 'stdout_tail': cp.stdout[-600:]}
    (EV / 'host-scan-raw.json').write_text(json.dumps(results, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps({k: (v.get('error') or 'ok') for k, v in results.items()}, indent=1))


if __name__ == '__main__':
    main()
