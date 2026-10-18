#!/usr/bin/env python3
"""恢复本例owned child/proxy，重启前后校验隔离身份。"""
import json
import backend_cases as h

SOURCE = r'''import hashlib,json,os,pwd,stat,subprocess,time
from pathlib import Path
owned=Path("/tmp/qa-resume-20261008-child-ciivjupc")
expected_dir=(33,135,65534); original_manifest="475c97667c8475ddb6dc4cc8d24a1f225af47e8af949de17fa8354dafc76b940"
patched_proxy="41195191953e7c0a95a0951d2991fb9459414c99a69a868c540a6febc6882537"
binary_sha="d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
def emit(v): print(json.dumps(v,separators=(",",":")),flush=True)
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def start(pid):
 raw=Path("/proc",str(pid),"stat").read_text(); return int(raw[raw.rfind(")")+2:].split()[19])
def uid(pid):
 for r in Path("/proc",str(pid),"status").read_text().splitlines():
  if r.startswith("Uid:"): return int(r.split()[1])
 raise RuntimeError("uid_missing")
def argv(pid): return [x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(bytes([0])) if x]
def gone(pid):
 try: start(pid); return False
 except OSError: return True
ds=owned.lstat(); mp=owned/"owner-manifest.json"; raw=mp.read_bytes(); owner=json.loads(raw)
if (ds.st_dev,ds.st_ino,ds.st_uid)!=(expected_dir[0],expected_dir[1],expected_dir[2]) or hashlib.sha256(raw).hexdigest()!=original_manifest: raise RuntimeError("recovery_owner_manifest_or_directory_guard_failed")
if owner.get("node_id")!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or owner.get("agent_id")!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5" or (owned/"owner.marker").read_text()!=owner.get("owner_marker"): raise RuntimeError("recovery_identity_guard_failed")
if sha(owned/"nodesteer-agent")!=binary_sha or sha(owned/"ws_proxy.py")!=patched_proxy: raise RuntimeError("recovery_binary_or_proxy_sha_guard_failed")
if not gone(int(owner["child"]["pid"])) or not gone(int(owner["supervisor"]["pid"])): raise RuntimeError("unexpected_owned_process_alive_recovery_aborted")
ready=owned/"supervisor.ready.json"
if ready.exists():
 if ready.is_symlink(): raise RuntimeError("ready_path_symlink")
 ready.unlink()
gid=pwd.getpwnam("nobody").pw_gid
def drop(): os.setgroups([]); os.setgid(gid); os.setuid(65534)
supervisor_path=owned/"supervisor.py"; controls=owned/"controls.json"; events=owned/"proxy-events.jsonl"
with (owned/"supervisor.log").open("ab") as log:
 p=subprocess.Popen([owner["supervisor"]["exe_path"],str(supervisor_path),str(owned),owner["proxy"]["bind_ip"],"192.168.100.209","18080",str(controls),str(events),str(ready)],cwd=str(owned),stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,start_new_session=True,preexec_fn=drop,env={"PATH":"/usr/bin:/bin","HOME":str(owned)})
end=time.monotonic()+10
while time.monotonic()<end and not ready.is_file():
 if p.poll() is not None: raise RuntimeError("proxy_supervisor_exited")
 time.sleep(.1)
if not ready.is_file() or ready.is_symlink(): raise RuntimeError("proxy_ready_missing")
port=int(json.loads(ready.read_text()).get("port",0))
if not 1<=port<=65535: raise RuntimeError("proxy_port_invalid")
config=Path(owner["config"]["path"]); cs=config.lstat()
if config.is_symlink() or stat.S_IMODE(cs.st_mode)!=0o600 or cs.st_uid!=65534: raise RuntimeError("child_config_private_mode_guard_failed")
values={}
for line in config.read_text().splitlines():
 k,sep,v=line.partition(":")
 if not sep: raise RuntimeError("child_config_line_shape_invalid")
 try: values[k.strip()]=json.loads(v.strip())
 except Exception: values[k.strip()]=v.strip()
if values.get("agent_id")!=owner["agent_id"] or hashlib.sha256(values.get("registration_token","").encode()).hexdigest()[:16]!=owner["registration_token_fingerprint"]: raise RuntimeError("child_config_identity_or_fingerprint_guard_failed")
values["hub_url"]="ws://"+owner["proxy"]["bind_ip"]+":"+str(port)+"/"
lines=[k+": "+(json.dumps(v,separators=(",",":")) if isinstance(v,str) else ("true" if v is True else "false" if v is False else json.dumps(v,separators=(",",":")))) for k,v in values.items()]
temp=config.with_suffix(".yaml.tmp"); temp.write_text("\n".join(lines)+"\n"); os.chmod(temp,0o600); os.chown(temp,65534,65534); temp.replace(config)
supervisor={"pid":p.pid,"starttime":start(p.pid),"uid":65534,"exe_path":os.readlink("/proc/"+str(p.pid)+"/exe"),"exe_sha256":sha("/proc/"+str(p.pid)+"/exe"),"argv_owner_path_match":any(a==str(owned) or a.startswith(str(owned)+os.sep) for a in argv(p.pid))}
if supervisor["exe_sha256"]!=owner["supervisor"]["exe_sha256"] or not supervisor["argv_owner_path_match"]: raise RuntimeError("recovered_supervisor_process_proof_failed")
owner["proxy"]["port"]=port; owner["proxy"]["source_sha256"]=patched_proxy; owner["proxy"]["original_source_sha256"]="fb8b1a4c6421a5d27dcafcb25b4d01ff5dae4328c86c075e4fef6398c4b9b6cf"; owner["supervisor"]=supervisor
with mp.open("w") as f: json.dump(owner,f,separators=(",",":"))
os.chmod(mp,0o600); os.chown(mp,65534,65534)
with (owned/"agent.log").open("ab") as log:
 child=subprocess.Popen([str(owned/"nodesteer-agent"),"--config",str(config)],cwd=str(owned),stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,start_new_session=True,preexec_fn=drop,env={"PATH":"/usr/bin:/bin","HOME":str(owned)})
time.sleep(1)
if child.poll() is not None: raise RuntimeError("child_restart_exited")
childrow={"pid":child.pid,"starttime":start(child.pid),"uid":65534,"exe_path":os.readlink("/proc/"+str(child.pid)+"/exe"),"exe_sha256":sha("/proc/"+str(child.pid)+"/exe"),"argv_owner_path_match":any(a==str(owned) or a.startswith(str(owned)+os.sep) for a in argv(child.pid))}
if childrow["exe_sha256"]!=binary_sha or not childrow["argv_owner_path_match"]: raise RuntimeError("recovered_child_process_proof_failed")
owner["child"]=childrow
with mp.open("w") as f: json.dump(owner,f,separators=(",",":"))
os.chmod(mp,0o600); os.chown(mp,65534,65534)
emit({"status":"PASS","node_id":owner["node_id"],"agent_id":owner["agent_id"],"directory_dev":ds.st_dev,"directory_ino":ds.st_ino,"owner_manifest_sha256":sha(mp),"proxy_source_sha256":patched_proxy,"proxy_port":port,"child":childrow,"supervisor":supervisor,"child_config_mode":"0o600","child_config_uid":config.stat().st_uid,"secret_values_emitted":False})
'''


def main():
    result = h.create_native4_command('BE-SCRIPT-SHA-002-recover-proxy-observer', SOURCE, timeout=45)
    if result.get('status') != 'SUCCESS' or not isinstance(result.get('output'), dict) or result['output'].get('status') != 'PASS':
        raise RuntimeError('proxy_observer_recovery_failed')
    output = result['output']
    h.atomic(h.HERE / 'owned-process-ledger.json', output)
    h.checkpoint('BE-SCRIPT-SHA-002', 'proxy-observer-recovered', **output)
    print(json.dumps({'status': 'PASS', 'proxy_source_sha256': output['proxy_source_sha256'], 'proxy_port': output['proxy_port'], 'manifest_sha256': output['owner_manifest_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
