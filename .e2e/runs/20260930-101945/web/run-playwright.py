import json, subprocess, os, time, sys
from pathlib import Path
private = Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
data = json.loads((private / 'connection.json').read_text())
output = Path(__file__).parent.resolve()
label = sys.argv[1]
args = sys.argv[2:]
env = os.environ.copy()
env.update(BASE_URL=data['base_url'], NODESTEER_E2E_USERNAME=data['admin_username'], NODESTEER_E2E_PASSWORD=data['admin_password'], NODESTEER_E2E_PREFIX='qa-20260930-101945-web-', NODESTEER_E2E_NODE_IDS='56765828-4896-492b-b562-64afdc0fb8d6', NODESTEER_E2E_LABEL_NODE_ID='1c59cf12-97ef-412f-9a06-aea04e6423c1', NODESTEER_E2E_OUTPUT_DIR=str(private / 'playwright-artifacts'), NODESTEER_E2E_RESOURCE_LOG=str(output / 'playwright-resources.jsonl'), NODESTEER_E2E_APP_ELF=str(private/'runweb/agent1-shell.elf'), NODESTEER_E2E_GUEST_INSPECT=str(output/'guest-inspect.py'), NODESTEER_E2E_APP_DIR='/tmp/qa-20260930-101945-web', NODESTEER_E2E_EVIDENCE_DIR=str(output/'evidence'), NODESTEER_E2E_PRIVATE_DIR=str(private/'runweb'))
env['NODESTEER_E2E_SCRIPT_CACHE_INSPECT']=str(output/'inspect-script-cache.py')
with (private / (label + '-raw.json')).open('w') as stream:
 result = subprocess.run(['node', 'node_modules/playwright/cli.js', 'test', '--config', 'e2e/playwright.config.ts', '--reporter=json'] + args, cwd='/home/superc/code/nodesteer/web', env=env, stdout=stream, stderr=subprocess.PIPE, text=True, timeout=900)
text = (private / (label + '-raw.json')).read_text()
for key in ['admin_password', 'admin_token']:
 if data.get(key): text = text.replace(data[key], '[REDACTED]')
secrets_file=private/'runweb/ui-users.jsonl'
if secrets_file.exists():
 for line in secrets_file.read_text().splitlines():
  item=json.loads(line)
  for key in ['password','token']:
   if item.get(key):text=text.replace(item[key],'[REDACTED]')
(output / (label + '-results.json')).write_text(text)
(output / (label + '-run.json')).write_text(json.dumps({'exit_code': result.returncode, 'completed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'args': args, 'stderr': result.stderr[:500]}, ensure_ascii=False, indent=2))
try: print(json.dumps({'round': label, 'exit_code': result.returncode, 'stats': json.loads(text).get('stats')}, ensure_ascii=False))
except ValueError: print('Result requires inspection; exit', result.returncode)
