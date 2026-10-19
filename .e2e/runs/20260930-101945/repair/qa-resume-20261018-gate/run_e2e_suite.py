#!/usr/bin/env python3
"""qa-resume-20261018-gate: full Playwright E2E suite (web/e2e, 20 files).

BASE_URL=http://192.168.100.209:18080; retries=0 (first-run truth), workers=1.
Adapted from qa-resume-20261018-final2-verify/run_e2e_suite.py (per-file JSON,
secret redaction, resource ledger). Private fixtures stay outside the repo.
"""
import json
import os
import subprocess
import sys
import time
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
RAW = HERE / 'raw'
PRIVATE = pathlib.Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/qa-gate/private')
TMP = pathlib.Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/qa-gate/tmp')
for d in (RAW, HERE / 'evidence', PRIVATE, TMP):
    d.mkdir(parents=True, exist_ok=True)
os.chmod(PRIVATE, 0o700)
os.umask(0o077)

CRED_DIR = pathlib.Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
cred = json.loads((CRED_DIR / 'repair-credentials.json').read_text())
BASE_URL = 'http://192.168.100.209:18080'

rbac_file = pathlib.Path('/tmp/opencode/nodesteer-pw-20261018/rbac-users.json')
rb = json.loads(rbac_file.read_text())

# Deterministic role account: reuse the existing viewer role account from the
# copied private store instead of provisioning a new permanent user.
role_username = None
role_password = None
store = PRIVATE / 'ui-users.jsonl'
if store.exists():
    for line in store.read_text().splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get('role_account') and item.get('username') and item.get('password'):
            role_username, role_password = item['username'], item['password']
            break

env = os.environ.copy()
env.update(
    BASE_URL=BASE_URL,
    NODESTEER_E2E_USERNAME=cred['admin_username'],
    NODESTEER_E2E_PASSWORD=cred['admin_password'],
    NODESTEER_E2E_PREFIX='qa-gate-',
    NODESTEER_E2E_NODE_IDS='91f44866-8a28-435e-bbda-2f2367126c00',
    NODESTEER_E2E_LABEL_NODE_ID='611c02a5-1859-4465-878b-d543b7f6fdec',
    NODESTEER_E2E_TRANSFER_TARGET_IDS='611c02a5-1859-4465-878b-d543b7f6fdec,37989297-3bc8-4c0c-b302-7c6efbdba630',
    NODESTEER_E2E_RESOURCE_LOG=str(HERE / 'playwright-resources.jsonl'),
    NODESTEER_E2E_EVIDENCE_DIR=str(HERE / 'evidence'),
    NODESTEER_E2E_PRIVATE_DIR=str(PRIVATE),
    NODESTEER_E2E_OPERATOR_USERNAME=rb['users']['operator']['username'],
    NODESTEER_E2E_VIEWER_USERNAME=rb['users']['viewer']['username'],
    NODESTEER_E2E_RBAC_PASSWORD=rb['password'],
    TMPDIR=str(TMP),
)
if role_username:
    env['NODESTEER_E2E_ROLE_USERNAME'] = role_username
    env['NODESTEER_E2E_ROLE_PASSWORD'] = role_password

label = sys.argv[1] if len(sys.argv) > 1 else 'gate-full'
env['NODESTEER_E2E_OUTPUT_DIR'] = str(HERE / 'artifacts' / label)
raw_private = PRIVATE / f'{label}.raw.json'
raw_path = RAW / f'{label}.json'
t0 = time.time()
with raw_private.open('w') as stream:
    proc = subprocess.run(
        ['node', 'node_modules/playwright/cli.js', 'test', '--config', 'e2e/playwright.config.ts',
         '--reporter=json'] + sys.argv[2:],
        cwd='/home/superc/code/nodesteer/web', env=env,
        stdout=stream, stderr=subprocess.PIPE, text=True,
        timeout=int(os.environ.get('QA_FILE_TIMEOUT', '5400')),
    )
dur = round(time.time() - t0, 1)
text = raw_private.read_text()

# Redact secrets before persisting.
secrets = [cred.get('admin_password'), cred.get('registration_token'), rb.get('password'), role_password]
if store.exists():
    for line in store.read_text().splitlines():
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
raw_path.write_text(text)

try:
    data = json.loads(text)
except ValueError:
    data = None
summary = {'label': label, 'exit_code': proc.returncode, 'duration_sec': dur,
           'base_url': BASE_URL, 'stderr': proc.stderr[:800]}
if data:
    st = data.get('stats', {})
    summary['stats'] = {k: st.get(k) for k in ('expected', 'unexpected', 'skipped', 'flaky', 'duration') if k in st}
    failures, skips, passes, flaky = [], [], [], []
    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get('title'), str) and 'tests' in node and 'file' in node:
                for t in node['tests']:
                    statuses = [r.get('status') for r in t.get('results', [])]
                    final = statuses[-1] if statuses else None
                    entry = {'file': node.get('file'), 'title': node['title'], 'status': final,
                             'statuses': statuses,
                             'annotations': t.get('annotations') or []}
                    if final == 'failed':
                        failures.append(entry)
                    elif final == 'skipped':
                        skips.append(entry)
                    elif final == 'passed':
                        passes.append(entry)
                    if len(statuses) > 1 and 'failed' in statuses and final == 'passed':
                        flaky.append(entry)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(data.get('suites', []))
    summary['passed_count'] = len(passes)
    summary['failed_count'] = len(failures)
    summary['skipped_count'] = len(skips)
    summary['flaky'] = flaky
    summary['failed_titles'] = failures
    summary['skipped_titles'] = skips
    summary['passed_titles'] = passes
    # per-file durations
    files = {}
    def walkf(node):
        if isinstance(node, dict):
            if isinstance(node.get('file'), str) and 'specs' in node:
                files.setdefault(node['file'], 0)
                for spec in node.get('specs', []):
                    for t in spec.get('tests', []):
                        for r in t.get('results', []):
                            files[node['file']] += r.get('duration', 0)
            for v in node.values():
                walkf(v)
        elif isinstance(node, list):
            for v in node:
                walkf(v)
    walkf(data.get('suites', []))
    summary['per_file_duration_ms'] = dict(sorted(files.items()))
(RAW / f'{label}-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
if label == 'gate-full':
    (HERE / 'e2e-suite.raw-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps({k: summary[k] for k in summary if k not in ('failed_titles', 'skipped_titles', 'per_file_duration_ms')},
                 ensure_ascii=False, indent=1)[:4000])
