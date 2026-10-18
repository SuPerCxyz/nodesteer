#!/usr/bin/env python3
"""Verify the exact Native-2 TLS test root and tools before the first remote write."""
import json
import os
import shutil
import stat
import subprocess
import uuid
from pathlib import Path

import api

HERE=Path(__file__).resolve().parent
PLAN=json.loads((HERE/"plan.json").read_text())
NODE_ID=PLAN["target_native2_node_id"]
ROOT=PLAN["remote_owned_root"]
EXPECTED_SHA=PLAN["expected_agent_sha256"]
UNITS=list(PLAN["units"].values())

def main():
    before=api.get_json("/api/nodes/"+NODE_ID)
    if before.get("id")!=NODE_ID or before.get("status")!="online" or before.get("sync_status")!="synced":
        raise RuntimeError("native2_preflight_health_gate_failed")
    source=r'''import hashlib,json,os,shutil,stat,subprocess
from pathlib import Path
ROOT=Path(%r); EXPECTED_SHA=%r; UNITS=%r
def sha(p):
 h=hashlib.sha256()
 try:
  with open(p,"rb") as f:
   for b in iter(lambda:f.read(1048576),b""):h.update(b)
  return h.hexdigest()
 except OSError:return None
def run(args,timeout=10):
 try:
  p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
  return {"returncode":p.returncode,"stdout":p.stdout.strip()}
 except FileNotFoundError:return {"returncode":127,"stdout":""}
 except subprocess.TimeoutExpired:return {"returncode":124,"stdout":"timeout"}
pid=os.getpid(); chain=[]; found=None; seen=set()
while pid>1 and pid not in seen and len(chain)<24:
 seen.add(pid); p=Path("/proc",str(pid))
 try:
  raw=(p/"stat").read_text();tail=raw[raw.rfind(")")+2:].split();ppid=int(tail[1]);start=int(tail[19]);exe=os.readlink(str(p/"exe"));h=sha(exe)
 except (OSError,ValueError,IndexError):break
 row={"pid":pid,"ppid":ppid,"starttime":start,"exe":exe,"sha256":h,"repair_sha_match":h==EXPECTED_SHA};chain.append(row)
 if h==EXPECTED_SHA:found=row;break
 pid=ppid
if ROOT.exists() or ROOT.is_symlink():root={"exists":True,"is_symlink":ROOT.is_symlink(),"realpath":str(ROOT.resolve())}
else:root={"exists":False,"parent_realpath":str(ROOT.parent.resolve())}
units={}
for u in UNITS:
 p=Path("/etc/systemd/system")/u
 x=run(["systemctl","show","--property=LoadState","--value",u])
 units[u]={"path_exists":p.exists() or p.is_symlink(),"systemd_load_state":x["stdout"],"systemctl_returncode":x["returncode"]}
if shutil.which("openssl"):
 o=run([shutil.which("openssl"),"version"]); openssl={"available":True,"path":shutil.which("openssl"),"version":o["stdout"],"returncode":o["returncode"]}
else:openssl={"available":False}
docker_path=shutil.which("docker"); docker={"binary":docker_path}
if docker_path:
 x=run([docker_path,"info","--format","{{.ServerVersion}}|{{.DockerRootDir}}|{{.Driver}}|{{.CgroupVersion}}"])
 docker["server"]={"reachable":x["returncode"]==0,"returncode":x["returncode"],"fields":x["stdout"].split("|") if x["returncode"]==0 else []}
 im=run([docker_path,"image","ls","--digests","--no-trunc","--format","{{json .}}"],15)
 images=[]
 if im["returncode"]==0:
  for line in im["stdout"].splitlines()[:128]:
   try:
    r=json.loads(line);images.append({"repository":r.get("Repository"),"tag":r.get("Tag"),"digest":r.get("Digest"),"id":r.get("ID"),"size":r.get("Size")})
   except (ValueError,TypeError):pass
 docker["image_list"]={"returncode":im["returncode"],"row_count":len(images),"images":images}
else:
 docker["server"]={"reachable":False};docker["image_list"]={"returncode":127,"row_count":0,"images":[]}
nsenter=shutil.which("nsenter");ns={"path":nsenter,"available":bool(nsenter)}
if nsenter:
 ns["version"]=run([nsenter,"--version"])["stdout"].splitlines()[:1];h=run([nsenter,"--help"]);ns["root_supported"]="--root" in h["stdout"]
try:
 st=Path("/opt").stat();d=shutil.disk_usage("/opt");disk={"opt_realpath":str(Path("/opt").resolve()),"uid":st.st_uid,"mode":oct(stat.S_IMODE(st.st_mode)),"free_bytes":d.free}
except OSError:disk={"available":False}
rootunit={}
for name in ("/proc/1/ns/mnt","/proc/1/ns/pid","/etc/systemd/system"):
 try:
  st=Path(name).stat();rootunit[name]={"exists":True,"uid":st.st_uid,"mode":oct(stat.S_IMODE(st.st_mode))}
 except OSError:rootunit[name]={"exists":False}
print(json.dumps({"status":"PASS","root":root,"units":units,"openssl":openssl,"docker":docker,"nsenter":ns,"opt":disk,"host_prereqs":rootunit,"repair_agent_ancestor_found":bool(found),"repair_agent_ancestor":found,"process_ancestors":chain,"side_effects":False},separators=(",",":")))
''' % (ROOT,EXPECTED_SHA,UNITS)
    command="python3 - <<'PY'\n"+source.rstrip()+"\nPY"
    suffix=uuid.uuid4().hex[:8]
    task=api.create_task("qa-resume-20261009-host-tls-preflight-"+suffix,command,NODE_ID)
    result=None; cleanup=None
    try: result=api.run_task(task)
    finally: cleanup=api.cleanup_task(task["id"],NODE_ID)
    if result.get("status")!="SUCCESS":raise RuntimeError("native2_preflight_task_failed")
    try: output=json.loads(result.get("stdout") or "")
    except (ValueError,TypeError):raise RuntimeError("native2_preflight_output_invalid")
    if output.get("status")!="PASS" or output.get("side_effects") is not False:raise RuntimeError("native2_preflight_assertion_failed")
    after=api.get_json("/api/nodes/"+NODE_ID)
    record={"status":"PASS","authority":api.HUB,"nonce":PLAN["nonce"],"node_before":{k:before.get(k) for k in ("id","hostname","ip","status","sync_status","agent_version")},"node_after":{k:after.get(k) for k in ("id","hostname","ip","status","sync_status","agent_version")},"node_unchanged_online_synced":after.get("status")=="online" and after.get("sync_status")=="synced","probe":output,"task_id":task["id"],"execution_id":result["id"],"task_cleanup":cleanup,"remote_writes":False}
    api.write_json("preflight-readonly.json",record)
    print(json.dumps({"status":"PASS","nonce":PLAN["nonce"],"remote_root_absent":output.get("root",{}).get("exists") is False,"all_units_absent":all(not x.get("path_exists") and x.get("systemd_load_state")=="not-found" for x in output.get("units",{}).values()),"openssl_available":output.get("openssl",{}).get("available"),"docker_server_reachable":output.get("docker",{}).get("server",{}).get("reachable"),"docker_image_list_rc":output.get("docker",{}).get("image_list",{}).get("returncode"),"docker_image_rows":output.get("docker",{}).get("image_list",{}).get("row_count"),"agent_sha_matches":output.get("repair_agent_ancestor",{}).get("repair_sha_match"),"task_id":task["id"],"execution_id":result["id"],"task_get404":cleanup.get("get_after_delete_http")==404}))

if __name__=="__main__":main()
