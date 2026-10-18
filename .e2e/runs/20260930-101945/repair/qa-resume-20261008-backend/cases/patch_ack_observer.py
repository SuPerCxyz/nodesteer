#!/usr/bin/env python3
"""在本例proxy记录真实SYNC_ACK的最少安全摘要。"""
import ast
import json
import backend_cases as h

SOURCE = r'''import hashlib,json,os,pwd,signal,stat,subprocess,time
from pathlib import Path
p=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";owner=json.loads(mp.read_bytes())
manifest_sha="2c8f726f550a4d6543a33f2db55a4331a95ba1978e0cd6cfe79bb42d5e0acc30";proxy_sha="d9ac209a20ddda8a6101e80a9d42ddaa9be49af7892f403a98ef6e3b57c0cd57"
def sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()
def start(pid):
 raw=Path("/proc",str(pid),"stat").read_text();return int(raw[raw.rfind(")")+2:].split()[19])
def uid(pid):
 for r in Path("/proc",str(pid),"status").read_text().splitlines():
  if r.startswith("Uid:"):return int(r.split()[1])
 raise RuntimeError("uid_missing")
def argv(pid):return [x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(bytes([0])) if x]
def match(row):
 pid=int(row["pid"]);return start(pid)==int(row["starttime"]) and uid(pid)==int(row["uid"])==65534 and sha("/proc/"+str(pid)+"/exe")==row["exe_sha256"] and any(a==str(p) or a.startswith(str(p)+os.sep) for a in argv(pid))
def stop(row):
 pid=int(row["pid"])
 if not match(row):raise RuntimeError("owned_supervisor_identity_guard_failed_no_signal")
 os.kill(pid,signal.SIGTERM);end=time.monotonic()+6
 while time.monotonic()<end:
  try:
   if start(pid)!=row["starttime"]:return
  except OSError:return
  time.sleep(.1)
 if match(row):os.kill(pid,signal.SIGKILL)
 end=time.monotonic()+3
 while time.monotonic()<end:
  try:
   if start(pid)!=row["starttime"]:return
  except OSError:return
  time.sleep(.1)
 raise RuntimeError("supervisor_stop_timeout")
ds=p.lstat();raw=mp.read_bytes();child=owner["child"];sup=owner["supervisor"]
if (ds.st_dev,ds.st_ino,ds.st_uid)!=(33,135,65534) or sha(mp)!=manifest_sha or owner["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or owner["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5" or (p/"owner.marker").read_text()!=owner["owner_marker"]:raise RuntimeError("owned_identity_guard_failed")
try:start(child["pid"]);raise RuntimeError("child_not_stopped")
except FileNotFoundError:pass
proxy=p/"ws_proxy.py";source=proxy.read_text()
if sha(proxy)!=proxy_sha or "import hashlib" not in source:raise RuntimeError("proxy_sha_or_hashlib_guard_failed")
anchor="                    kind = message.get('type', '')\n"
addition="                    if direction == 'up' and kind == 'SYNC_ACK':\n                        ack = message.get('payload', {})\n                        error = str(ack.get('error', ''))\n                        self.log(event='sync_ack', connection=connection_id, direction=direction, type=kind, id=message.get('id', ''), ok=ack.get('ok'), applied_rev=ack.get('applied_rev'), error_sha256=hashlib.sha256(error.encode()).hexdigest(), error_chars=len(error))\n"
if source.count(anchor)!=1 or "event='sync_ack'" in source:raise RuntimeError("sync_ack_patch_anchor_mismatch")
source=source.replace(anchor,anchor+addition,1);new_sha=hashlib.sha256(source.encode()).hexdigest()
if not match(sup):raise RuntimeError("owned_supervisor_proof_failed")
stop(sup);proxy.write_text(source);os.chmod(proxy,0o400);os.chown(proxy,65534,65534)
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
if not ready.is_file() or ready.is_symlink():raise RuntimeError("supervisor_ready_missing")
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
print(json.dumps({"status":"PASS","node_id":owner["node_id"],"child_stopped":True,"supervisor":newsup,"proxy_port":port,"proxy_source_sha256":new_sha,"owner_manifest_sha256":sha(mp),"secret_values_emitted":False},separators=(",",":")))
'''


def main():
    ast.parse(SOURCE)
    result = h.create_native4_command('BE-SCRIPT-SHA-002-install-sync-ack-observer', SOURCE, timeout=45)
    if result.get('status') != 'SUCCESS' or not isinstance(result.get('output'), dict) or result['output'].get('status') != 'PASS':
        raise RuntimeError('sync_ack_observer_install_failed')
    output = result['output']
    ledger = json.loads((h.HERE / 'owned-process-ledger.json').read_text())
    ledger.update({'supervisor': output['supervisor'], 'proxy_port': output['proxy_port'], 'proxy_source_sha256': output['proxy_source_sha256'], 'owner_manifest_sha256': output['owner_manifest_sha256']})
    h.atomic(h.HERE / 'owned-process-ledger.json', ledger)
    h.checkpoint('BE-SCRIPT-SHA-002', 'sync-ack-observer-installed', child_stopped=True, proxy_port=output['proxy_port'], proxy_source_sha256=output['proxy_source_sha256'], owner_manifest_sha256=output['owner_manifest_sha256'])
    print(json.dumps({'status': 'PASS', 'proxy_port': output['proxy_port'], 'proxy_source_sha256': output['proxy_source_sha256'], 'owner_manifest_sha256': output['owner_manifest_sha256']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
