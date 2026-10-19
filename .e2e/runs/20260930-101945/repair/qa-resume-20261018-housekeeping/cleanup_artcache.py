#!/usr/bin/env python3
"""qa-resume-20261018-housekeeping: delete confirmed-owned QA leftover dirs.

Scope guard: only the four directories confirmed as this campaign's QA-owned
residue (nonce/run-id in name, evidence-backed) and verified inert in
predelete-inventory.json.  CAS-style: the remote script re-verifies the exact
inventory (empty skeleton, or exact file sha256 set) before rmtree.
"""
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = HERE / 'evidence'
os.umask(0o077)

KEY = str(Path.home() / '.ssh' / 'id_ed25519')
TARGETS = {
    'native-1': {
        'ip': '192.168.100.204',
        'paths': ['/opt/qa-appui-588e85afe10f', '/opt/qa-g5-9505276f0c'],
        'allow_files': False,
    },
    'native-3': {
        'ip': '192.168.100.222',
        'paths': ['/opt/nodesteer-qa-p11-05-b3ca14953c14'],
        'allow_files': False,
    },
}
# hub-209 /var/lib/nodesteer-hub-repair-20260930-101945-ttl-ui: confirmed QA-owned but
# contains a hub database (test data) and sibling config/unit files; disposition uncertain
# -> report only, no delete (per "不确定就只报告").

REMOTE = r'''
import hashlib, json, os, shutil, sys, time
plan = json.loads(sys.argv[1])
out = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'results': {}}
def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()
for item in plan:
    p = item['path']
    r = {'path': p, 'deleted': False}
    out['results'][p] = r
    if not (os.path.isdir(p) and not os.path.islink(p)):
        r['error'] = 'not_a_plain_directory'
        continue
    files = []
    for root, dnames, fnames in os.walk(p):
        for n in fnames:
            fp = os.path.join(root, n)
            files.append({'path': fp, 'sha256': sha(fp), 'size': os.lstat(fp).st_size})
    r['files_seen'] = files
    if item.get('allow_files'):
        expected = {f['path']: f['sha256'] for f in item['expected_files']}
        seen = {f['path']: f['sha256'] for f in files}
        if seen != expected:
            r['error'] = 'sha_set_mismatch'
            r['expected'] = expected
            continue
    else:
        if files:
            r['error'] = 'unexpected_files_in_empty_skeleton'
            continue
    try:
        shutil.rmtree(p)
    except Exception as exc:
        r['error'] = 'rmtree_%s' % type(exc).__name__
        continue
    r['deleted'] = not os.path.exists(p)
    r['parent_listing_after'] = sorted(os.listdir(os.path.dirname(p)))[:50]
print(json.dumps(out))
'''


def main():
    inv = json.loads((EV / 'predelete-inventory.json').read_text())
    results = {}
    for host, cfg in TARGETS.items():
        plan = []
        for p in cfg['paths']:
            entry = {'path': p, 'allow_files': cfg['allow_files']}
            if cfg['allow_files']:
                t = inv[host]['targets'][p]
                entry['expected_files'] = [{'path': f['path'], 'sha256': f['sha256']} for f in t['files']]
            plan.append(entry)
        script = REMOTE.replace('json.loads(sys.argv[1])', 'json.loads(%r)' % json.dumps(plan))
        cp = subprocess.run(
            ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'BatchMode=yes',
             '-o', 'ConnectTimeout=8', '-i', KEY, 'root@' + cfg['ip'],
             'python3', '-'],
            input=script, capture_output=True, text=True, timeout=150)
        lines = [l for l in cp.stdout.splitlines() if l.strip()]
        try:
            results[host] = json.loads(lines[-1])
        except Exception as exc:
            results[host] = {'error': 'parse_%s' % type(exc).__name__,
                             'stdout_tail': cp.stdout[-600:], 'stderr_tail': cp.stderr[-400:]}
    (EV / 'cleanup-exec.json').write_text(json.dumps(results, ensure_ascii=False, indent=1) + '\n')
    for host, v in results.items():
        if 'results' in v:
            for p, r in v['results'].items():
                print(host, p, '->', 'DELETED' if r.get('deleted') else ('ERROR ' + str(r.get('error'))))
        else:
            print(host, '->', v)


if __name__ == '__main__':
    main()
