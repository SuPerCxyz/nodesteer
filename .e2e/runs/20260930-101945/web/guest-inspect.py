import json, subprocess, sys
from pathlib import Path
private=Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
connection=json.loads((private/'connection.json').read_text())
node=next(n for n in connection['nodes'] if n['id']=='56765828-4896-492b-b562-64afdc0fb8d6')
unit, directory, mode=sys.argv[1:4]
assert unit.startswith(('qa-20260930-101945-web-app-','qa-20260930-101945-web-role-')) and unit.endswith('.service')
assert directory.startswith(('/tmp/qa-20260930-101945-web/app-','/tmp/qa-20260930-101945-web/role-')) and '..' not in directory
payload=json.dumps({'unit':unit,'directory':directory,'mode':mode})
script="python3 - <<'PYCODE'\n"+"import json,subprocess,pathlib,hashlib,shutil,os\n"+f"args=json.loads({payload!r})\n"+"""u=args['unit'];d=pathlib.Path(args['directory'])
if args['mode']=='cleanup':
 subprocess.run(['systemctl','stop',u],capture_output=True)
 subprocess.run(['systemctl','disable',u],capture_output=True)
 unit=pathlib.Path('/etc/systemd/system')/u
 if unit.exists():unit.unlink()
 subprocess.run(['systemctl','daemon-reload'],capture_output=True,check=True)
 if d.exists():shutil.rmtree(d)
 print(json.dumps({'unit':u,'unit_removed':not unit.exists(),'directory_removed':not d.exists()}))
else:
 result=subprocess.run(['systemctl','show',u,'--property=ActiveState,SubState,MainPID,Result'],capture_output=True,text=True)
 properties=dict(x.split('=',1) for x in result.stdout.splitlines() if '='in x)
 files={}
 for name in ['binary','config.ini']:
  p=d/name
  if p.exists():files[name]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':p.stat().st_size,'mode':oct(p.stat().st_mode&0o777),'content':p.read_text() if name=='config.ini' else None}
 p=pathlib.Path('/etc/systemd/system')/u
 print(json.dumps({'unit':u,'properties':properties,'files':files,'unit_content':p.read_text() if p.exists() else None}))
PYCODE
"""
helper=Path(__file__).resolve().parent.parent/'environment/guest-exec.py'
result=subprocess.run([sys.executable,str(helper),'--node',node['id'],'--timeout','30'],input=script,capture_output=True,text=True,timeout=35)
if result.returncode:raise RuntimeError(result.stderr[:400])
print(result.stdout)
