#!/usr/bin/env python3
"""Repair-env Playwright E2E runner: per-file JSON results, secret redaction."""
import json, os, subprocess, sys, time, pathlib, re

PRIVATE = pathlib.Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
WORK = pathlib.Path('/tmp/opencode/nodesteer-pw-20261018')
OUT = pathlib.Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-playwright')
RAW = WORK / 'raw'
RAW.mkdir(parents=True, exist_ok=True)
(OUT / 'evidence').mkdir(parents=True, exist_ok=True)

cred = json.loads((PRIVATE / 'repair-credentials.json').read_text())
BASE_URL = 'http://192.168.100.209:18080'

# Repair-env QA agents (owned by this repair run, qa- prefix), not production agents.
NODE_IDS = os.environ.get('QA_NODE_IDS', '91f44866-8a28-435e-bbda-2f2367126c00')
LABEL_NODE_ID = os.environ.get('QA_LABEL_NODE_ID', '611c02a5-1859-4465-878b-d543b7f6fdec')

env = os.environ.copy()
env.update(
    BASE_URL=BASE_URL,
    NODESTEER_E2E_USERNAME=cred['admin_username'],
    NODESTEER_E2E_PASSWORD=cred['admin_password'],
    NODESTEER_E2E_PREFIX=os.environ.get('QA_PREFIX', 'qa-pw-20261018-'),
    NODESTEER_E2E_NODE_IDS=NODE_IDS,
    NODESTEER_E2E_LABEL_NODE_ID=LABEL_NODE_ID,
    NODESTEER_E2E_OUTPUT_DIR=str(WORK / 'artifacts' / os.environ.get('QA_LABEL_HINT', 'default')),
    NODESTEER_E2E_RESOURCE_LOG=str(OUT / 'playwright-resources.jsonl'),
    NODESTEER_E2E_EVIDENCE_DIR=str(OUT / 'evidence'),
    NODESTEER_E2E_PRIVATE_DIR=str(WORK),
)
# Deliberately NOT set: NODESTEER_E2E_ALLOW_SHARED_SETTINGS (global settings must not change),
# NODESTEER_E2E_APP_ELF / GUEST_INSPECT / APP_DIR / SCRIPT_CACHE_INSPECT (need guest SSH, out of redline).

rbac_file = WORK / 'rbac-users.json'
if rbac_file.exists():
    rb = json.loads(rbac_file.read_text())
    env.update(
        NODESTEER_E2E_OPERATOR_USERNAME=rb['users']['operator']['username'],
        NODESTEER_E2E_VIEWER_USERNAME=rb['users']['viewer']['username'],
        NODESTEER_E2E_RBAC_PASSWORD=rb['password'],
    )


label = sys.argv[1]
os.environ["QA_LABEL_HINT"] = label
env['NODESTEER_E2E_OUTPUT_DIR'] = str(WORK / 'artifacts' / label)
args = sys.argv[2:]
raw_path = RAW / f'{label}.json'
t0 = time.time()
with raw_path.open('w') as stream:
    proc = subprocess.run(
        ['node', 'node_modules/playwright/cli.js', 'test', '--config', 'e2e/playwright.config.ts',
         '--reporter=json', '--trace=on'] + args,
        cwd='/home/superc/code/nodesteer/web', env=env,
        stdout=stream, stderr=subprocess.PIPE, text=True,
        timeout=int(os.environ.get('QA_FILE_TIMEOUT', '3600')),
    )
dur = round(time.time() - t0, 1)
text = raw_path.read_text()

# Redact secrets before persisting.
secrets = [cred.get('admin_password'), cred.get('registration_token')]
users_file = WORK / 'ui-users.jsonl'
if users_file.exists():
    for line in users_file.read_text().splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        for key in ('password', 'token', 'secret'):
            if item.get(key):
                secrets.append(item[key])
for s in secrets:
    if s and len(str(s)) > 6:
        text = text.replace(str(s), '[REDACTED]')
for s in secrets:
    if s and len(str(s)) > 6:
        for f in RAW.glob(f'{label}.json'):
            pass
(RAW / f'{label}.json').write_text(text)

try:
    data = json.loads(text)
except ValueError:
    data = None
summary = {'label': label, 'exit_code': proc.returncode, 'duration_sec': dur,
           'args': args, 'stderr': proc.stderr[:800]}
if data:
    st = data.get('stats', {})
    summary['stats'] = {k: st.get(k) for k in ('expected', 'unexpected', 'skipped', 'flaky', 'duration') if k in st}
(RAW / f'{label}-run.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False))
