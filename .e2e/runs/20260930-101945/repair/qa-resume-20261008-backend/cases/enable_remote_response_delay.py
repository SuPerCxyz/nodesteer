#!/usr/bin/env python3
"""在匹配Remote请求上增加仅传输延时，不改任何RemoteState字段。"""
import ast
import json
import backend_cases as h

SOURCE = r'''import asyncio,hashlib,json,os,pwd,signal,stat,subprocess,time
from pathlib import Path
p=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";owner=json.loads(mp.read_bytes())
expected_manifest=__MANIFEST__;expected_proxy=__PROXY__
def sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()
def start(pid):
 raw=Path("/proc",str(pid),"stat").read_text();return int(raw[raw.rfind(")")+2:].split()[19])
def uid(pid):
 for r in Path("/proc",str(pid),"status").read_text().splitlines():
  if r.startswith("Uid:"):return int(r.split()[1])
 raise RuntimeError("uid_missing")
def argv(pid):return [x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(bytes([0])) if x]
def matches(row):
 pid=int(row["pid"]);return start(pid)==int(row["starttime"]) and uid(pid)==int(row["uid"])==65534 and sha("/proc/"+str(pid)+"/exe")==row["exe_sha256"] and any(a==str(p) or a.startswith(str(p)+os.sep) for a in argv(pid))
def stop(row):
 pid=int(row["pid"])
 if not matches(row):raise RuntimeError("owned_process_identity_guard_failed_no_signal")
 os.kill(pid,signal.SIGTERM);end=time.monotonic()+6
 while time.monotonic()<end:
  try:
   if start(pid)!=row["starttime"]:return
  except OSError:return
  time.sleep(.1)
 if matches(row):os.kill(pid,signal.SIGKILL)
 end=time.monotonic()+3
 while time.monotonic()<end:
  try:
   if start(pid)!=row["starttime"]:return
  except OSError:return
  time.sleep(.1)
 raise RuntimeError("owned_process_stop_timeout")
ds=p.lstat();raw=mp.read_bytes();child=owner["child"];sup=owner["supervisor"]
if (ds.st_dev,ds.st_ino,ds.st_uid)!=(33,135,65534) or sha(mp)!=expected_manifest or owner["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or owner["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5" or (p/"owner.marker").read_text()!=owner["owner_marker"]:raise RuntimeError("owned_identity_guard_failed")
if sha(p/"ws_proxy.py")!=expected_proxy or sha(p/"nodesteer-agent")!="d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add" or json.loads((p/"controls.json").read_text())!={}:raise RuntimeError("source_binary_or_control_guard_failed")
if not matches(child) or not matches(sup):raise RuntimeError("owned_process_identity_guard_failed_no_signal")
proxy=p/"ws_proxy.py";source=proxy.read_text();anchor="                        if kind == 'REMOTE_STATE' and controls.get('expire_remote'):\n"
insert="""                        if kind == 'REMOTE_STATE' and direction == 'down' and isinstance(override, dict):
                            delay_request_matches = request.get('target_node_id') == override.get('target_node_id') and request.get('property') == override.get('property') and request.get('task_id') == override.get('task_id')
                            delay_ms = int(override.get('delay_ms', 0)) if delay_request_matches else 0
                            if delay_ms < 0 or delay_ms > 1000:
                                raise ValueError('remote delivery delay outside test bound')
                            if delay_ms:
                                self.log(event='remote_delivery_delay', connection=connection_id, direction=direction, type=kind, id=message.get('id', ''), request=request, delay_ms=delay_ms, payload_sha256=hashlib.sha256(json.dumps(message.get('payload', {}), sort_keys=True).encode()).hexdigest())
                                await asyncio.sleep(delay_ms / 1000)
"""+anchor
if source.count(anchor)!=1 or "event='remote_delivery_delay'" in source:raise RuntimeError("remote_delivery_delay_patch_anchor_failed")
source=source.replace(anchor,insert,1);new_sha=hashlib.sha256(source.encode()).hexdigest();stop(child);stop(sup);proxy.write_text(source);os.chmod(proxy,0o400);os.chown(proxy,65534,65534)
ready=p/"supervisor.ready.json"
if ready.exists():
 if ready.is_symlink():raise RuntimeError("ready_symlink")
 ready.unlink()
gid=pwd.getpwnam("nobody").pw_gid;drop=lambda:(os.setgroups([]),os.setgid(gid),os.setuid(65534))
with (p/"supervisor.log").open("ab") as log:proc=subprocess.Popen([sup["exe_path"],str(p/"supervisor.py"),str(p),owner["proxy"]["bind_ip"],"192.168.100.209","18080",str(p/"controls.json"),str(p/"proxy-events.jsonl"),str(ready)],cwd=str(p),stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,start_new_session=True,preexec_fn=drop,env={"PATH":"/usr/bin:/bin","HOME":str(p)})
end=time.monotonic()+10
while time.monotonic()<end and not ready.is_file():
 if proc.poll() is not None:raise RuntimeError("supervisor_restart_exited")
 time.sleep(.1)
if not ready.is_file() or ready.is_symlink():raise RuntimeError("proxy_ready_missing")
port=int(json.loads(ready.read_text()).get("port",0))
if not 1<=port<=65535:raise RuntimeError("proxy_port_invalid")
config=Path(owner["config"]["path"]);cs=config.lstat()
if config.is_symlink() or stat.S_IMODE(cs.st_mode)!=0o600 or cs.st_uid!=65534:raise RuntimeError("child_config_private_guard_failed")
values={}
for line in config.read_text().splitlines():
 k,sep,v=line.partition(":")
 if not sep:raise RuntimeError("child_config_shape_invalid")
 try:values[k.strip()]=json.loads(v.strip())
 except Exception:values[k.strip()]=v.strip()
if values.get("agent_id")!=owner["agent_id"] or hashlib.sha256(values.get("registration_token","").encode()).hexdigest()[:16]!=owner["registration_token_fingerprint"]:raise RuntimeError("child_config_identity_or_fingerprint_failed")
values["hub_url"]="ws://"+owner["proxy"]["bind_ip"]+":"+str(port)+"/"
lines=[k+": "+(json.dumps(v,separators=(",",":")) if isinstance(v,str) else ("true" if v is True else "false" if v is False else json.dumps(v,separators=(",",":")))) for k,v in values.items()]
tmp=config.with_suffix(".yaml.tmp");tmp.write_text("\n".join(lines)+"\n");os.chmod(tmp,0o600);os.chown(tmp,65534,65534);tmp.replace(config)
newsup={"pid":proc.pid,"starttime":start(proc.pid),"uid":65534,"exe_path":os.readlink("/proc/"+str(proc.pid)+"/exe"),"exe_sha256":sha("/proc/"+str(proc.pid)+"/exe"),"argv_owner_path_match":any(a==str(p) or a.startswith(str(p)+os.sep) for a in argv(proc.pid))}
if newsup["exe_sha256"]!=sup["exe_sha256"] or not newsup["argv_owner_path_match"]:raise RuntimeError("new_supervisor_proof_failed")
owner["proxy"]["port"]=port;owner["proxy"]["source_sha256"]=new_sha;owner["proxy"]["original_source_sha256"]="fb8b1a4c6421a5d27dcafcb25b4d01ff5dae4328c86c075e4fef6398c4b9b6cf";owner["supervisor"]=newsup
with mp.open("w") as f:json.dump(owner,f,separators=(",",":"))
os.chmod(mp,0o600);os.chown(mp,65534,65534)
with (p/"agent.log").open("ab") as log:cp=subprocess.Popen([str(p/"nodesteer-agent"),"--config",str(config)],cwd=str(p),stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,start_new_session=True,preexec_fn=drop,env={"PATH":"/usr/bin:/bin","HOME":str(p)})
time.sleep(1)
if cp.poll() is not None:raise RuntimeError("child_restart_exited")
newchild={"pid":cp.pid,"starttime":start(cp.pid),"uid":65534,"exe_path":os.readlink("/proc/"+str(cp.pid)+"/exe"),"exe_sha256":sha("/proc/"+str(cp.pid)+"/exe"),"argv_owner_path_match":any(a==str(p) or a.startswith(str(p)+os.sep) for a in argv(cp.pid))}
if newchild["exe_sha256"]!="d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add" or not newchild["argv_owner_path_match"]:raise RuntimeError("new_child_process_proof_failed")
owner["child"]=newchild
with mp.open("w") as f:json.dump(owner,f,separators=(",",":"))
os.chmod(mp,0o600);os.chown(mp,65534,65534)
print(json.dumps({"status":"PASS","node_id":owner["node_id"],"agent_id":owner["agent_id"],"proxy_source_sha256":new_sha,"proxy_port":port,"owner_manifest_sha256":sha(mp),"child":newchild,"supervisor":newsup,"secret_values_emitted":False},separators=(",",":")))
'''


def main():
    ledger=json.loads((h.HERE/'owned-process-ledger.json').read_text())
    source=SOURCE.replace('__MANIFEST__',json.dumps(ledger['owner_manifest_sha256'])).replace('__PROXY__',json.dumps(ledger['proxy_source_sha256']))
    ast.parse(source)
    result=h.create_native4_command('BE-REMOTE-TTL-enable-observed-response-delay',source,timeout=60)
    if result.get('status')!='SUCCESS' or not isinstance(result.get('output'),dict) or result['output'].get('status')!='PASS':raise RuntimeError('remote_response_delay_install_failed')
    out=result['output'];h.atomic(h.HERE/'owned-process-ledger.json',out)
    h.checkpoint('BE-REMOTE-TTL-004','response-delay-enabled',proxy_source_sha256=out['proxy_source_sha256'],proxy_port=out['proxy_port'],owner_manifest_sha256=out['owner_manifest_sha256'],child=out['child'],supervisor=out['supervisor'])
    print(json.dumps({'status':'PASS','proxy_source_sha256':out['proxy_source_sha256'],'proxy_port':out['proxy_port'],'owner_manifest_sha256':out['owner_manifest_sha256']},ensure_ascii=False))


if __name__=='__main__':main()
