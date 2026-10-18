#!/usr/bin/env python3
"""Fill the real NodeSteer login UI without printing or persisting credentials."""
import json
import subprocess
from pathlib import Path

PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO')
SESSION = 'qa-p002-v1'
HOST = '192.168.100.209'
creds = json.loads((PRIVATE / 'repair-credentials.json').read_text())
username = creds['admin_username']
password = creds['admin_password']
script = (
    'const vals=' + json.dumps([username, password]) + ';'
    'const u=document.querySelector("input[name=username]");'
    'const p=document.querySelector("input[name=password]");'
    'if(!u||!p) throw new Error("login inputs missing");'
    'const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set;'
    'set.call(u,vals[0]);u.dispatchEvent(new Event("input",{bubbles:true}));'
    'u.dispatchEvent(new Event("change",{bubbles:true}));'
    'set.call(p,vals[1]);p.dispatchEvent(new Event("input",{bubbles:true}));'
    'p.dispatchEvent(new Event("change",{bubbles:true}));'
    '({usernameFilled:u.value.length>0,passwordFilled:p.value.length>0})'
)
result = subprocess.run(
    ['agent-browser', '--session', SESSION, '--allowed-domains', HOST, 'eval', '--stdin'],
    input=script, text=True, capture_output=True, timeout=20)
username = password = None
if result.returncode != 0:
    raise SystemExit('agent-browser failed to fill login fields; details withheld')
print(json.dumps({'login_fields_filled': True, 'credential_values_output': False}))
