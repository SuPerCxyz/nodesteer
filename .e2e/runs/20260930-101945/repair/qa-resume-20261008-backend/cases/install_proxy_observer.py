#!/usr/bin/env python3
"""给本例独占proxy增加只记录脚本ID/SHA的帧摘要，不记录任何内容。"""
import hashlib
import json
import backend_cases as h

SOURCE = r'''import hashlib,json,os,pwd,signal,stat,subprocess,sys,time
from pathlib import Path
owned=Path("/tmp/qa-resume-20261008-child-ciivjupc")
expected=(33,135,65534,"475c97667c8475ddb6dc4cc8d24a1f225af47e8af949de17fa8354dafc76b940")
binary_sha="d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
proxy_sha="fb8b1a4c6421a5d27dcafcb25b4d01ff5dae4328c86c075e4fef6398c4b9b6cf"
def emit(v): print(json.dumps(v,separators=(",",":")),flush=True)
def file_sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def start(pid):
 raw=Path("/proc",str(pid),"stat").read_text(); return int(raw[raw.rfind(")")+2:].split()[19])
def uid(pid):
 for row in Path("/proc",str(pid),"status").read_text().splitlines():
  if row.startswith("Uid:"): return int(row.split()[1])
 raise RuntimeError("uid_missing")
def argv(pid): return [x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(b"\0") if x]
def matches(row):
 p=int(row["pid"]); return start(p)==int(row["starttime"]) and uid(p)==int(row["uid"])==65534 and file_sha("/proc/"+str(p)+"/exe")==row["exe_sha256"] and any(a==str(owned) or a.startswith(str(owned)+os.sep) for a in argv(p))
def stop(row):
 p=int(row["pid"])
 if not matches(row): raise RuntimeError("owned_process_identity_guard_failed_no_signal")
 os.kill(p,signal.SIGTERM); end=time.monotonic()+6
 while time.monotonic()<end:
  try:
   if start(p)!=int(row["starttime"]): return
  except OSError: return
  time.sleep(.1)
 if matches(row): os.kill(p,signal.SIGKILL)
 end=time.monotonic()+3
 while time.monotonic()<end:
  try:
   if start(p)!=int(row["starttime"]): return
  except OSError: return
  time.sleep(.1)
 raise RuntimeError("owned_process_stop_timeout")
ds=owned.lstat(); mp=owned/"owner-manifest.json"; raw=mp.read_bytes(); msha=hashlib.sha256(raw).hexdigest(); owner=json.loads(raw)
if (ds.st_dev,ds.st_ino,ds.st_uid)!=(expected[0],expected[1],expected[2]) or msha!=expected[3] or owner.get("node_id")!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or owner.get("agent_id")!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5": raise RuntimeError("owned_directory_or_identity_guard_failed")
if file_sha(owned/"nodesteer-agent")!=binary_sha or (owned/"owner.marker").read_text()!=owner.get("owner_marker"): raise RuntimeError("owner_marker_or_binary_guard_failed")
proxy=owned/"ws_proxy.py"
if proxy.is_symlink() or file_sha(proxy)!=proxy_sha: raise RuntimeError("proxy_source_sha_guard_failed")
source=proxy.read_text()
anchor="""                        fields = {'connection': connection_id, 'direction': direction, 'type': kind, 'id': message.get('id', '')}\n"""
insert=anchor+"""                        if kind == 'SYNC_RESPONSE':
                            entries = message.get('payload', {}).get('scripts', [])
                            fields['script_summary'] = [{'id': x.get('id'), 'revision': x.get('revision'), 'declared_sha256': (x.get('data') or {}).get('sha256'), 'content_sha256': hashlib.sha256(((x.get('data') or {}).get('content') or '').encode()).hexdigest()} for x in entries if isinstance(x, dict)]
"""
if source.count(anchor)!=1: raise RuntimeError("proxy_frame_summary_anchor_mismatch")
source=source.replace(anchor,insert)
anchor2="self.log(event='corrupt_script_sha', target_id=target, **fields)"
insert2="self.log(event='corrupt_script_sha', target_id=target, declared_sha256=script['data'].get('sha256'), received_content_sha256=hashlib.sha256(script['data']['content'].encode()).hexdigest(), **fields)"
if source.count(anchor2)!=1: raise RuntimeError("proxy_corrupt_log_anchor_mismatch")
source=source.replace(anchor2,insert2)
patched_sha=hashlib.sha256(source.encode()).hexdigest()
if patched_sha==proxy_sha: raise RuntimeError("proxy_source_patch_noop")
sup=owner['supervisor']; child=owner['child']
if not matches(sup) or not matches(child): raise RuntimeError("owned_process_identity_guard_failed_no_signal")
stop(child); stop(sup)
proxy.write_text(source); os.chmod(proxy,0o400); os.chown(proxy,65534,65534)
for rel in ['supervisor.ready.json']:
 p=owned/rel
 if p.exists() and not p.is_symlink(): p.unlink()
gid=pwd.getpwnam('nobody').pw_gid
def drop(): os.setgroups([]); os.setgid(gid); os.setuid(65534)
supervisor_path=owned/'supervisor.py'; controls=owned/'controls.json'; events=owned/'proxy-events.jsonl'; ready=owned/'supervisor.ready.json'
with (owned/'supervisor.log').open('ab') as log:
 p=subprocess.Popen([sup['exe_path'],str(supervisor_path),owner['proxy']['bind_ip'],'192.168.100.209','18080',str(controls),str(events),str(ready)],cwd=str(owned),stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,start_new_session=True,preexec_fn=drop,env={'PATH':'/usr/bin:/bin','HOME':str(owned)})
end=time.monotonic()+10
while time.monotonic()<end and not ready.is_file():
 if p.poll() is not None: raise RuntimeError("proxy_supervisor_exited")
 time.sleep(.1)
if not ready.is_file() or ready.is_symlink(): raise RuntimeError("proxy_ready_missing")
port=int(json.loads(ready.read_text()).get('port',0))
if not 1<=port<=65535: raise RuntimeError("proxy_port_invalid")
config=Path(owner['config']['path']); cs=config.lstat()
if config.is_symlink() or stat.S_IMODE(cs.st_mode)!=0o600 or cs.st_uid!=65534: raise RuntimeError("child_config_private_mode_guard_failed")
values={}
for line in config.read_text().splitlines():
 k,sep,v=line.partition(':')
 if not sep: raise RuntimeError("child_config_line_shape_invalid")
 key=k.strip(); value=v.strip()
 try: values[key]=json.loads(value)
 except Exception: values[key]=value
if values.get('agent_id')!=owner['agent_id'] or not isinstance(values.get('registration_token'),str) or hashlib.sha256(values['registration_token'].encode()).hexdigest()[:16]!=owner['registration_token_fingerprint']: raise RuntimeError("child_config_identity_or_fingerprint_guard_failed")
values['hub_url']='ws://'+owner['proxy']['bind_ip']+':'+str(port)+'/'
lines=[]
for k,v in values.items(): lines.append(k+': '+(json.dumps(v,separators=(',',':')) if isinstance(v,str) else ('true' if v is True else 'false' if v is False else json.dumps(v,separators=(',',':')))))
temp=config.with_suffix('.yaml.tmp'); temp.write_text('\n'.join(lines)+'\n'); os.chmod(temp,0o600); os.chown(temp,65534,65534); temp.replace(config)
supervisor={'pid':p.pid,'starttime':start(p.pid),'uid':65534,'exe_path':os.readlink('/proc/'+str(p.pid)+'/exe'),'exe_sha256':file_sha('/proc/'+str(p.pid)+'/exe'),'argv_owner_path_match':True}
if file_sha('/proc/'+str(p.pid)+'/exe')!=sup['exe_sha256'] or not any(a==str(owned) or a.startswith(str(owned)+os.sep) for a in argv(p.pid)): raise RuntimeError("new_supervisor_proof_failed")
owner['proxy']['port']=port; owner['proxy']['source_sha256']=patched_sha; owner['proxy']['original_source_sha256']=proxy_sha; owner['supervisor']=supervisor
with mp.open('w') as f: json.dump(owner,f,separators=(',',':'))
os.chmod(mp,0o600); os.chown(mp,65534,65534)
manifest_sha=file_sha(mp)
uid_info=pwd.getpwnam('nobody')
with (owned/'agent.log').open('ab') as log:
 childp=subprocess.Popen([str(owned/'nodesteer-agent'),'--config',str(config)],cwd=str(owned),stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,start_new_session=True,preexec_fn=drop,env={'PATH':'/usr/bin:/bin','HOME':str(owned)})
time.sleep(1)
if childp.poll() is not None: raise RuntimeError("child_restart_exited")
childrow={'pid':childp.pid,'starttime':start(childp.pid),'uid':65534,'exe_path':os.readlink('/proc/'+str(childp.pid)+'/exe'),'exe_sha256':file_sha('/proc/'+str(childp.pid)+'/exe'),'argv_owner_path_match':True}
if childrow['exe_sha256']!=binary_sha or not any(a==str(owned) or a.startswith(str(owned)+os.sep) for a in argv(childp.pid)): raise RuntimeError("new_child_proof_failed")
owner['child']=childrow
with mp.open('w') as f: json.dump(owner,f,separators=(',',':'))
os.chmod(mp,0o600); os.chown(mp,65534,65534)
manifest_sha=file_sha(mp)
emit({'status':'PASS','node_id':owner['node_id'],'agent_id':owner['agent_id'],'directory_dev':ds.st_dev,'directory_ino':ds.st_ino,'owner_manifest_sha256':manifest_sha,'proxy_original_source_sha256':proxy_sha,'proxy_source_sha256':patched_sha,'proxy_port':port,'supervisor':supervisor,'child':childrow,'child_config_mode':'0o600','child_config_uid':config.stat().st_uid,'secret_values_emitted':False})
'''


def main():
    result = h.create_native4_command('BE-SCRIPT-SHA-002-install-proxy-observer', SOURCE, timeout=45)
    if result.get('status') != 'SUCCESS' or not isinstance(result.get('output'), dict) or result['output'].get('status') != 'PASS':
        raise RuntimeError('proxy_observer_install_failed')
    output = result['output']
    h.atomic(h.HERE / 'owned-process-ledger.json', output)
    h.checkpoint('BE-SCRIPT-SHA-002', 'proxy-observer-installed', proxy_source_sha256=output['proxy_source_sha256'], proxy_port=output['proxy_port'], child=output['child'], supervisor=output['supervisor'], owner_manifest_sha256=output['owner_manifest_sha256'])
    print(json.dumps({'status': 'PASS', 'proxy_source_sha256': output['proxy_source_sha256'], 'proxy_port': output['proxy_port'], 'manifest_sha256': output['owner_manifest_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
