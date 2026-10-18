#!/usr/bin/env python3
"""Inspect or precisely clean one FULL-08 QA application on repair Native-1."""
import hashlib,json,pathlib,shlex,subprocess,sys
RUN='20260930-101945';PRIV=pathlib.Path('/tmp/nodesteer-e2e-'+RUN+'-repair')
conn=json.loads((PRIV/'repair-connection.json').read_text());unit,directory,mode=sys.argv[1:4]
if not unit.startswith('qa-20260930-101945-web-app-') or not unit.endswith('.service') or '/' in unit:raise SystemExit('unit outside authorized exact QA prefix')
if not directory.startswith('/tmp/qa-repair-20260930-101945-app-web/app-') or '..' in pathlib.PurePosixPath(directory).parts:raise SystemExit('application directory outside exact QA prefix')
node=conn['nodes'][0];source=json.loads(pathlib.Path('/tmp/nodesteer-e2e-'+RUN+'-7wksl0_x/connection.json').read_text());key=next(n['ssh_identity_file'] for n in source['nodes'] if n['ip']==node['ssh_ip'])
remote='\n'.join(['set -eu',f'u={shlex.quote(unit)}',f'd={shlex.quote(directory)}',
 'if test "$1" = cleanup; then',
 '  systemctl stop "$u" >/dev/null 2>&1 || true',
 '  systemctl disable "$u" >/dev/null 2>&1 || true',
 '  f=/etc/systemd/system/"$u"; test "$f" = /etc/systemd/system/' + unit,
 '  if test -e "$f"; then rm -f -- "$f"; fi',
 '  systemctl daemon-reload',
 '  case "$d" in /tmp/qa-repair-20260930-101945-app-web/app-*) ;; *) exit 72 ;; esac',
 '  if test -d "$d"; then rm -rf -- "$d"; fi',
 '  printf \'{"unit_removed":%s,"directory_removed":%s}\\n\' "$(test ! -e "$f" && echo true || echo false)" "$(test ! -e "$d" && echo true || echo false)"',
 'else',
 '  python3 - "$u" "$d" <<\'PY2\'',
 'import hashlib,json,pathlib,subprocess,sys',
 'u,d=sys.argv[1:];r=subprocess.run(["systemctl","show",u,"--property=ActiveState,SubState,MainPID,Result"],text=True,capture_output=True);props=dict(x.split("=",1) for x in r.stdout.splitlines() if "=" in x)',
 'base=pathlib.Path(d);files={}',
 'for name in ("binary","config.ini"):',
 ' p=base/name',
 ' if p.exists():files[name]={"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"size":p.stat().st_size,"mode":oct(p.stat().st_mode&0o777),"content":p.read_text() if name=="config.ini" else None}',
 'unit=pathlib.Path("/etc/systemd/system")/u',
 'print(json.dumps({"properties":props,"files":files,"unit_content":unit.read_text() if unit.exists() else None}))',
 'PY2',
 'fi',''])
argv=['ssh','-F','/dev/null','-T','-o','BatchMode=yes','-o','ConnectTimeout=10','-o','LogLevel=ERROR','-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=/dev/null','-i',key,'-p',str(node['ssh_port']),node['ssh_target'],'sh','-s',mode]
p=subprocess.run(argv,input=remote,text=True,capture_output=True,timeout=40)
if p.returncode:raise RuntimeError('repair guest inspection/cleanup failed; output suppressed')
print(p.stdout.strip())
