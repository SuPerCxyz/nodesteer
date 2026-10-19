#!/usr/bin/env python3
"""Redact credentials from Playwright trace.zip evidence (byte-level replace, rezip)."""
import json, pathlib, zipfile, shutil, sys

OUT = pathlib.Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-playwright')
WORK = pathlib.Path('/tmp/opencode/nodesteer-pw-20261018')
cred = json.loads(pathlib.Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json').read_text())
rb = json.loads((WORK / 'rbac-users.json').read_text())

secrets = [cred['admin_password'], cred['registration_token'], rb['password']]
uf = WORK / 'ui-users.jsonl'
if uf.exists():
    for line in uf.read_text().splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        for k in ('password', 'token'):
            if item.get(k):
                secrets.append(item[k])
secrets = [s for s in {str(x) for x in secrets} if len(s) > 6]

report = []
for zp in sorted((OUT / 'evidence' / 'traces').rglob('trace.zip')):
    tmp = zp.with_suffix('.zip.tmp')
    hits = 0
    with zipfile.ZipFile(zp) as zin, zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            for s in secrets:
                b = s.encode()
                if b in data:
                    hits += data.count(b)
                    data = data.replace(b, b'[REDACTED]')
            zout.writestr(info, data)
    shutil.move(tmp, zp)
    report.append({'trace': str(zp.relative_to(OUT)), 'redactions': hits})

# re-scan
left = []
for p in OUT.rglob('*'):
    if not p.is_file():
        continue
    if p.suffix == '.zip':
        try:
            with zipfile.ZipFile(p) as z:
                for n in z.namelist():
                    data = z.read(n)
                    for s in secrets:
                        if s.encode() in data:
                            left.append(f'{p}!{n}')
        except Exception:
            pass
    else:
        t = p.read_text(errors='ignore')
        for s in secrets:
            if s in t:
                left.append(str(p))
print(json.dumps({'sanitized': report, 'remaining_leaks': left}, ensure_ascii=False, indent=1))
