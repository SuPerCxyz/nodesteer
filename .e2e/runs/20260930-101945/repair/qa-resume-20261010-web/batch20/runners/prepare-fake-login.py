import subprocess,secrets,hashlib,json
from pathlib import Path
s='qawebunauth20261012-b20'
username='qa-invalid-login-b20-'+secrets.token_hex(8)
password='not-a-real-password-'+secrets.token_hex(8)
def run(*args):return subprocess.run(['agent-browser','--session',s,*args],capture_output=True,text=True,timeout=5,check=True)
run('fill','input[autocomplete="username"]',username)
run('fill','input[autocomplete="current-password"]',password)
run('focus','input[autocomplete="current-password"]')
# Only hashes/lengths are persisted; raw fake values disappear with this process.
obj={'username_sha256':hashlib.sha256(username.encode()).hexdigest(),'username_length':len(username),'password_sha256':hashlib.sha256(password.encode()).hexdigest(),'password_length':len(password),'username_is_nonexistent_unique_prefix':True,'password_is_non_real_dummy':True,'dom_input_values_saved':False}
Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch20/evidence/P01-04-fake-input-metadata.json').write_text(json.dumps(obj,indent=2)+'\n')
print(json.dumps({'inputs_filled':True,'username_length':obj['username_length'],'password_length':obj['password_length'],'only_hashes_saved':True}))
