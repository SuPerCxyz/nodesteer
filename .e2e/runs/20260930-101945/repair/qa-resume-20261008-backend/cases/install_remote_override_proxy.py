#!/usr/bin/env python3
"""扩展本例私密proxy，仅按Remote request ID与Task/Property白名单覆盖一个响应。"""
import ast
import json
from pathlib import Path
import backend_cases as h

SOURCE = r'''import hashlib,json,os,pwd,signal,stat,subprocess,time
from pathlib import Path
p=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";owner=json.loads(mp.read_bytes())
expected_manifest="3400846d0f81f7934ffd69dc819b5ca21135e59b5e5a51489da4cbdcea482c17";expected_proxy="7573c838dc562af47c5903c6f72c1c3ab4b8a4c7f3a0f8ee3042f753ad51103d"
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
if sha(p/"ws_proxy.py")!=expected_proxy or sha(p/"nodesteer-agent")!="d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add":raise RuntimeError("source_or_binary_sha_guard_failed")
if json.loads((p/"controls.json").read_text())!={} or not matches(child) or not matches(sup):raise RuntimeError("controls_or_process_guard_failed")
proxy=p/"ws_proxy.py";source=proxy.read_text()
anchor1="            held, reordered, replays = [], [], []\n"
if source.count(anchor1)!=1 or "remote_requests = {}" in source:raise RuntimeError("remote_request_map_anchor_failed")
source=source.replace(anchor1,anchor1+"            remote_requests = {}\n",1)
anchor2="                    if direction == 'down' and kind in CONTROL_TYPES:\n"
insert2="""                    if direction == 'up' and kind == 'REMOTE_STATE_REQ':
                        request = message.get('payload', {})
                        request_id = message.get('id', '')
                        remote_requests[request_id] = {'target_node_id': request.get('target_node_id'), 'property': request.get('property'), 'task_id': request.get('task_id', '')}
                        self.log(event='remote_request', connection=connection_id, direction=direction, type=kind, id=request_id, **remote_requests[request_id])
"""+anchor2
if source.count(anchor2)!=1 or "event='remote_request'" in source:raise RuntimeError("remote_request_observer_anchor_failed")
source=source.replace(anchor2,insert2,1)
anchor3="                        if kind == 'REMOTE_STATE' and controls.get('expire_remote'):\n"
insert3="""                        if kind == 'REMOTE_STATE' and direction == 'down':
                            request_id = message.get('id', '')
                            request = remote_requests.pop(request_id, {})
                            override = controls.get('remote_override', {})
                            if isinstance(override, dict) and request.get('target_node_id') == override.get('target_node_id') and request.get('property') == override.get('property') and request.get('task_id') == override.get('task_id'):
                                data = message.get('payload', {})
                                before = {key: data.get(key) for key in ['value', 'observed_at', 'ttl_sec']}
                                for key in ['value', 'observed_at', 'ttl_sec']:
                                    if key in override: data[key] = override[key]
                                after = {key: data.get(key) for key in ['value', 'observed_at', 'ttl_sec']}
                                raw = encode(json.dumps(message).encode(), first & 0x0f, mask)
                                self.log(event='remote_override', connection=connection_id, direction=direction, type=kind, id=request_id, request=request, before=before, after=after)
"""+anchor3
if source.count(anchor3)!=1 or "event='remote_override'" in source:raise RuntimeError("remote_override_anchor_failed")
source=source.replace(anchor3,insert3,1)
new_sha=hashlib.sha256(source.encode()).hexdigest();stop(child);stop(sup);proxy.write_text(source);os.chmod(proxy,0o400);os.chown(proxy,65534,65534)
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
if newsup["exe_sha256"]!=sup["exe_sha256"] or not newsup["argv_owner_path_match"]:raise RuntimeError("new_supervisor_process_proof_failed")
owner["proxy"]["port"]=port;owner["proxy"]["source_sha256"]=new_sha;owner["proxy"]["original_source_sha256"]="fb8b1a4c6421a5d27dcafcb25b4d01ff5dae4328c86c075e4fef6398c4b9b6cf";owner["supervisor"]=newsup
with mp.open("w") as f:json.dump(owner,f,separators=(",",":"))
os.chmod(mp,0o600);os.chown(mp,65534,65534)
with (p/"agent.log").open("ab") as log:childproc=subprocess.Popen([str(p/"nodesteer-agent"),"--config",str(config)],cwd=str(p),stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,start_new_session=True,preexec_fn=drop,env={"PATH":"/usr/bin:/bin","HOME":str(p)})
time.sleep(1)
if childproc.poll() is not None:raise RuntimeError("child_restart_exited")
newchild={"pid":childproc.pid,"starttime":start(childproc.pid),"uid":65534,"exe_path":os.readlink("/proc/"+str(childproc.pid)+"/exe"),"exe_sha256":sha("/proc/"+str(childproc.pid)+"/exe"),"argv_owner_path_match":any(a==str(p) or a.startswith(str(p)+os.sep) for a in argv(childproc.pid))}
if newchild["exe_sha256"]!="d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add" or not newchild["argv_owner_path_match"]:raise RuntimeError("new_child_process_proof_failed")
owner["child"]=newchild
with mp.open("w") as f:json.dump(owner,f,separators=(",",":"))
os.chmod(mp,0o600);os.chown(mp,65534,65534)
print(json.dumps({"status":"PASS","node_id":owner["node_id"],"agent_id":owner["agent_id"],"proxy_source_sha256":new_sha,"proxy_port":port,"owner_manifest_sha256":sha(mp),"child":newchild,"supervisor":newsup,"child_config_mode":"0o600","child_config_uid":config.stat().st_uid,"secret_values_emitted":False},separators=(",",":")))
'''


def main():
    ast.parse(SOURCE)
    result = h.create_native4_command('BE-REMOTE-TTL-install-remote-override-proxy', SOURCE, timeout=60)
    if result.get('status') != 'SUCCESS' or not isinstance(result.get('output'), dict) or result['output'].get('status') != 'PASS':
        raise RuntimeError('remote_override_proxy_install_failed')
    out = result['output']
    h.atomic(h.HERE/'owned-process-ledger.json', out)
    h.checkpoint('BE-REMOTE-TTL-004','proxy-installed', proxy_source_sha256=out['proxy_source_sha256'], proxy_port=out['proxy_port'], owner_manifest_sha256=out['owner_manifest_sha256'], child=out['child'], supervisor=out['supervisor'])
    print(json.dumps({'status':'PASS','proxy_source_sha256':out['proxy_source_sha256'],'proxy_port':out['proxy_port'],'owner_manifest_sha256':out['owner_manifest_sha256']},ensure_ascii=False))


if __name__=='__main__':
    main()
