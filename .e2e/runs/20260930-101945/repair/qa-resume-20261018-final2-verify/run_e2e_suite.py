#!/usr/bin/env python3
"""qa-resume-20261018-final2-verify: run the full Playwright E2E suite (item 14).

BASE_URL=http://192.168.100.209:18080. Adapted from ~/p12-verify/run-pw.py
(per-file JSON results, secret redaction, resource ledger). Private fixtures
(passwords) stay outside the run dir; the resource log records cleanup state.
"""
import json
import os
import subprocess
import sys
import time
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SUITE = HERE / 'evidence' / 'e2e-suite'
RAW = SUITE / 'raw'
PRIVATE = pathlib.Path('/home/superc/final2-verify/private')
TMP = pathlib.Path('/home/superc/final2-verify/tmp')
for d in (RAW, SUITE / 'evidence', PRIVATE, TMP):
    d.mkdir(parents=True, exist_ok=True)
os.chmod(PRIVATE, 0o700)
os.umask(0o077)

CRED_DIR = pathlib.Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
cred = json.loads((CRED_DIR / 'repair-credentials.json').read_text())
BASE_URL = 'http://192.168.100.209:18080'

env = os.environ.copy()
env.update(
    BASE_URL=BASE_URL,
    NODESTEER_E2E_USERNAME=cred['admin_username'],
    NODESTEER_E2E_PASSWORD=cred['admin_password'],
    NODESTEER_E2E_PREFIX='qa-pw2-20261018-',
    NODESTEER_E2E_NODE_IDS='91f44866-8a28-435e-bbda-2f2367126c00',
    NODESTEER_E2E_LABEL_NODE_ID='611c02a5-1859-4465-878b-d543b7f6fdec',
    NODESTEER_E2E_RESOURCE_LOG=str(SUITE / 'playwright-resources.jsonl'),
    NODESTEER_E2E_EVIDENCE_DIR=str(SUITE / 'evidence'),
    NODESTEER_E2E_PRIVATE_DIR=str(PRIVATE),
    TMPDIR=str(TMP),
)

rbac_file = pathlib.Path('/tmp/opencode/nodesteer-pw-20261018/rbac-users.json')
if rbac_file.exists():
    rb = json.loads(rbac_file.read_text())
    env.update(
        NODESTEER_E2E_OPERATOR_USERNAME=rb['users']['operator']['username'],
        NODESTEER_E2E_VIEWER_USERNAME=rb['users']['viewer']['username'],
        NODESTEER_E2E_RBAC_PASSWORD=rb['password'],
    )
# Deliberately NOT set: redline-gated vars (guest SSH / offline orchestrator / script cache).
for key in ('NODESTEER_E2E_TRANSFER_TARGET_IDS', 'NODESTEER_E2E_OFFLINE_NODE_ID',
            'NODESTEER_E2E_OFFLINE_ORCHESTRATOR'):
    if os.environ.get(key):
        env[key] = os.environ[key]

label = sys.argv[1] if len(sys.argv) > 1 else 'final2-full'
env['NODESTEER_E2E_OUTPUT_DIR'] = str(SUITE / 'artifacts' / label)
raw_path = RAW / f'{label}.json'
t0 = time.time()
with raw_path.open('w') as stream:
    proc = subprocess.run(
        ['node', 'node_modules/playwright/cli.js', 'test', '--config', 'e2e/playwright.config.ts',
         '--reporter=json'] + (sys.argv[2:] or []),
        cwd='/home/superc/code/nodesteer/web', env=env,
        stdout=stream, stderr=subprocess.PIPE, text=True,
        timeout=int(os.environ.get('QA_FILE_TIMEOUT', '5400')),
    )
dur = round(time.time() - t0, 1)
text = raw_path.read_text()

# Redact secrets before persisting.
secrets = [cred.get('admin_password'), cred.get('registration_token')]
users_file = PRIVATE / 'ui-users.jsonl'
if users_file.exists():
    for line in users_file.read_text().splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        for key in ('password', 'token', 'secret'):
            if item.get(key):
                secrets.append(item[key])
if rbac_file.exists():
    secrets.append(rb.get('password'))
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
                             'statuses': statuses}
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
(RAW / f'{label}-run.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps({k: summary[k] for k in summary if k not in ('failed_titles', 'skipped_titles')},
                 ensure_ascii=False, indent=1)[:3000])
