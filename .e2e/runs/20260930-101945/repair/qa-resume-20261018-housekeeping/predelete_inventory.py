#!/usr/bin/env python3
"""qa-resume-20261018-housekeeping: pre-delete inventory (read-only).

Captures exact inventory + liveness checks for the four candidate QA-owned
leftover directories before any destructive action.  Writes
evidence/predelete-inventory.json.  No writes on hosts.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
os.umask(0o077)

KEY = str(Path.home() / '.ssh' / 'id_ed25519')
TARGETS = {
    'native-1': ('192.168.100.204', [
        '/opt/qa-appui-588e85afe10f',
        '/opt/qa-g5-9505276f0c',
    ]),
    'native-3': ('192.168.100.222', [
        '/opt/nodesteer-qa-p11-05-b3ca14953c14',
    ]),
    'hub-209': ('192.168.100.209', [
        '/var/lib/nodesteer-hub-repair-20260930-101945-ttl-ui',
    ]),
}

REMOTE = r'''
import hashlib, json, os, subprocess, sys, time
targets = json.loads(sys.stdin.read())
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'targets': {}}
def run(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return {'rc': r.returncode, 'out': r.stdout.strip()[:2000], 'err': r.stderr.strip()[:400]}
def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()
for t in targets:
    d = {'path': t}
    try:
        st = os.lstat(t)
        d['exists'] = True
        d['is_dir'] = os.path.isdir(t)
        d['is_symlink'] = os.path.islink(t)
        d['dev'] = st.st_dev
        d['inode'] = st.st_ino
        d['mode'] = oct(st.st_mode & 0o7777)
        d['uid'] = st.st_uid
        d['mtime'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(st.st_mtime))
    except FileNotFoundError:
        d['exists'] = False
        out['targets'][t] = d
        continue
    files = []
    dirs = []
    for root, dnames, fnames in os.walk(t):
        for n in dnames:
            p = os.path.join(root, n)
            dirs.append(p)
        for n in fnames:
            p = os.path.join(root, n)
            try:
                fs = os.lstat(p)
                files.append({'path': p, 'size': fs.st_size, 'mode': oct(fs.st_mode & 0o7777),
                              'sha256': sha(p) if fs.st_size < 20 * 1024 * 1024 else 'SKIP_LARGE'})
            except OSError as e:
                files.append({'path': p, 'error': type(e).__name__})
    d['dirs'] = sorted(dirs)
    d['files'] = files
    d['file_count'] = len(files)
    d['total_bytes'] = sum(f['size'] for f in files if 'size' in f)
    # liveness: any process referencing the path, any mount using it
    d['procs'] = [l for l in run("ps -eo pid,args --no-headers").get('out', '').splitlines() if t in l]
    d['mounts'] = [l for l in open('/proc/mounts') if t in l]
    d['fuser'] = run("fuser -m %s 2>&1 || true" % t)
    # open file descriptors pointing into the dir
    d['open_fds'] = run("ls -l /proc/*/fd 2>/dev/null | grep -F %s | head -10 || true" % t).get('out', '')
    out['targets'][t] = d
print(json.dumps(out))
'''


def main():
    results = {}
    for host, (ip, targets) in TARGETS.items():
        script = REMOTE.replace('targets = json.loads(sys.stdin.read())',
                                'targets = %r' % (targets,))
        cp = subprocess.run(
            ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
             '-o', 'ConnectTimeout=8', '-i', KEY, 'root@' + ip, 'python3', '-'],
            input=script, capture_output=True, text=True, timeout=150)
        lines = [l for l in cp.stdout.splitlines() if l.strip()]
        try:
            results[host] = json.loads(lines[-1])
        except Exception as exc:
            results[host] = {'error': 'parse_%s' % type(exc).__name__, 'stdout_tail': cp.stdout[-800:],
                             'stderr_tail': cp.stderr[-400:]}
    (EV / 'predelete-inventory.json').write_text(json.dumps(results, ensure_ascii=False, indent=1) + '\n')
    summary = {}
    for host, v in results.items():
        if 'targets' in v:
            summary[host] = {t: {'exists': d.get('exists'), 'file_count': d.get('file_count'),
                                 'dir_count': len(d.get('dirs', [])), 'procs': len(d.get('procs', [])),
                                 'mounts': len(d.get('mounts', []))} for t, d in v['targets'].items()}
        else:
            summary[host] = v
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    main()
