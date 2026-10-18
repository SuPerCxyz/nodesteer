#!/usr/bin/env python3
"""Run exactly one read-only Native-2 environment probe and remove its Task."""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

import test_api as api

HERE = Path(__file__).resolve().parent
NODE_ID = "611c02a5-1859-4465-878b-d543b7f6fdec"
BUILD = HERE.parent / "build-manifest.json"


def main():
    manifest = json.loads(BUILD.read_text())
    rows = [x for x in manifest.get("artifacts", []) if x.get("name") == "nodesteer-agent-static"]
    if len(rows) != 1 or not re.fullmatch(r"[0-9a-f]{64}", str(rows[0].get("sha256", ""))):
        raise RuntimeError("repair_agent_build_manifest_sha_missing_or_ambiguous")
    expected_sha = rows[0]["sha256"]
    before = api.get_json("/api/nodes/" + NODE_ID)
    if before.get("id") != NODE_ID or before.get("status") != "online" or before.get("sync_status") != "synced":
        raise RuntimeError("native2_not_online_synced")

    source = r'''import hashlib,json,os,re,shutil,stat,subprocess,time
from pathlib import Path
EXPECTED_AGENT_SHA=%r
def sha(path):
 h=hashlib.sha256()
 try:
  with open(path,"rb") as f:
   for b in iter(lambda:f.read(1048576),b""): h.update(b)
  return h.hexdigest()
 except (OSError,PermissionError): return None
def proc(pid):
 base=Path("/proc",str(pid))
 try:
  st=base.stat(); raw=(base/"stat").read_text(); tail=raw[raw.rfind(")")+2:].split(); ppid=int(tail[1]); start=int(tail[19])
  status=(base/"status").read_text().splitlines(); uid=int(next(x for x in status if x.startswith("Uid:")).split()[1]); caps=next(x for x in status if x.startswith("CapEff:")).split()[1]
  exe=os.readlink(str(base/"exe")); exe_sha=sha(exe); argv=[x.decode(errors="replace") for x in (base/"cmdline").read_bytes().split(b"\0") if x]
  config=None
  for i,x in enumerate(argv):
   if x=="--config" and i+1<len(argv): config=argv[i+1]
   elif x.startswith("--config="): config=x.split("=",1)[1]
  return {"pid":pid,"ppid":ppid,"starttime":start,"uid":uid,"exe":exe,"exe_sha256":exe_sha,"agent_sha_match":exe_sha==EXPECTED_AGENT_SHA,"cap_eff_hex":caps,"config_path":config if exe_sha==EXPECTED_AGENT_SHA else None}
 except (OSError,StopIteration,ValueError,IndexError): return None
def run(argv,timeout=8):
 try:
  x=subprocess.run(argv,text=True,capture_output=True,timeout=timeout)
  return {"available":True,"returncode":x.returncode,"stdout":x.stdout.strip()}
 except FileNotFoundError: return {"available":False,"returncode":None,"stdout":""}
 except subprocess.TimeoutExpired: return {"available":True,"returncode":124,"stdout":"timeout"}
def yaml_scalar(path,key):
 try: lines=Path(path).read_text().splitlines()
 except OSError: return None
 found=[x.split(":",1)[1].strip() for x in lines if re.match(r"^\s*"+re.escape(key)+r"\s*:",x)]
 if len(found)!=1:return None
 v=found[0]
 if v.startswith('"'):
  try:return json.loads(v)
  except (ValueError,TypeError):return None
 if v.startswith("'") and v.endswith("'"):return v[1:-1].replace("''", "'")
 return v.split("#",1)[0].strip()
pid=os.getpid(); chain=[]; seen=set(); agent=None
while pid>1 and pid not in seen and len(chain)<24:
 seen.add(pid); row=proc(pid)
 if not row:break
 chain.append({k:row[k] for k in ("pid","ppid","starttime","uid","exe","exe_sha256","agent_sha_match")})
 if row["agent_sha_match"]: agent=row; break
 pid=row["ppid"]
task_proc=proc(os.getpid())
if not task_proc:raise RuntimeError("task_process_probe_failed")
caps_map={1:"DAC_OVERRIDE",12:"NET_ADMIN",18:"SYS_CHROOT",19:"SYS_PTRACE",21:"SYS_ADMIN",24:"SYS_RESOURCE"}
capset=[]
bits=int(task_proc["cap_eff_hex"],16)
for bit,name in caps_map.items():
 if bits&(1<<bit):capset.append(name)
agent_config=None
if agent and agent.get("config_path"):
 cp=Path(agent["config_path"])
 try:
  st=cp.stat(); hub=yaml_scalar(cp,"hub_url"); ca=yaml_scalar(cp,"tls_ca_file"); data=yaml_scalar(cp,"data_dir"); mode=oct(stat.S_IMODE(st.st_mode)); owner=st.st_uid
  from urllib.parse import urlsplit
  u=urlsplit(hub or "")
  ca_stat=None
  if ca:
   try:
    x=Path(ca).stat();ca_stat={"exists":True,"mode":oct(stat.S_IMODE(x.st_mode)),"uid":x.st_uid,"size":x.st_size,"sha256":sha(ca)}
   except OSError:ca_stat={"exists":False}
  agent_config={"path":str(cp),"mode":mode,"uid":owner,"hub_scheme":u.scheme,"hub_host":u.hostname,"hub_port":u.port,"deployment_mode":yaml_scalar(cp,"deployment_mode"),"data_dir":data,"tls_ca_file_path":ca,"tls_ca_file":ca_stat}
 except OSError:agent_config={"path":str(cp),"readable":False}
docker_path=shutil.which("docker"); nsenter_path=shutil.which("nsenter")
docker={"binary":docker_path,"socket":None,"server":None,"images":[]}
sock=Path("/var/run/docker.sock")
try:
 x=sock.stat();docker["socket"]={"exists":True,"mode":oct(stat.S_IMODE(x.st_mode)),"uid":x.st_uid,"gid":x.st_gid,"is_socket":stat.S_ISSOCK(x.st_mode)}
except OSError:docker["socket"]={"exists":False}
if docker_path:
 info=run([docker_path,"info","--format","{{.ServerVersion}}|{{.DockerRootDir}}|{{.Driver}}|{{.CgroupVersion}}"])
 docker["server"]={"reachable":info["returncode"]==0,"returncode":info["returncode"],"fields":info["stdout"].split("|") if info["returncode"]==0 else []}
 if info["returncode"]==0:
  images=run([docker_path,"image","ls","--digests","--no-trunc","--format","{{json .}}"],timeout=12)
  if images["returncode"]==0:
   for line in images["stdout"].splitlines()[:128]:
    try:
     x=json.loads(line); repo=x.get("Repository",""); tag=x.get("Tag","")
     if repo!="<none>": docker["images"].append({"ref":repo+":"+tag,"digest":x.get("Digest"),"id":x.get("ID"),"size":x.get("Size")})
    except (ValueError,TypeError):pass
docker["known_agent_image_present"]=any(x["ref"].startswith("qa-20260930-101945-env-agent:") for x in docker["images"])
docker["nginx_image_present"]=any(x["ref"].startswith("nginx:") for x in docker["images"])
nsenter={"binary":nsenter_path,"available":bool(nsenter_path)}
if nsenter_path:
 nsenter["version"]=run([nsenter_path,"--version"])["stdout"].splitlines()[:1]
 help_result=run([nsenter_path,"--help"]);nsenter["root_option_supported"]="--root" in help_result["stdout"]
roots=[]
for name in ("/tmp","/var/tmp","/opt","/etc/systemd/system","/var/lib/nodesteer"):
 p=Path(name)
 try:
  st=p.stat();d=shutil.disk_usage(p);m=run(["findmnt","-no","FSTYPE,OPTIONS","-T",str(p)])
  roots.append({"path":name,"realpath":str(p.resolve()),"exists":True,"uid":st.st_uid,"gid":st.st_gid,"mode":oct(stat.S_IMODE(st.st_mode)),"writable_by_task":os.access(p,os.W_OK),"free_bytes":d.free,"mount":m["stdout"] if m["returncode"]==0 else "unknown"})
 except OSError:roots.append({"path":name,"exists":False})
result={"status":"PASS","expected_repair_agent_sha256":EXPECTED_AGENT_SHA,"task_process":{"pid":task_proc["pid"],"ppid":task_proc["ppid"],"starttime":task_proc["starttime"],"uid":task_proc["uid"],"exe":task_proc["exe"],"effective_capabilities_subset":capset},"process_ancestors":chain,"repair_agent_ancestor_found":bool(agent),"repair_agent_ancestor_sha256":agent.get("exe_sha256") if agent else None,"repair_agent_ancestor_sha_matches":bool(agent and agent.get("agent_sha_match")),"agent_config":agent_config,"docker":docker,"nsenter":nsenter,"filesystem_roots":roots,"probe_side_effects":False}
print(json.dumps(result,separators=(",",":")))
''' % expected_sha
    command = "python3 - <<'PY'\n" + source.rstrip() + "\nPY"
    safe_suffix = uuid.uuid4().hex[:10]
    task_name = "qa-resume-20261009-host-tls-native2-readonly-" + safe_suffix
    task = api.create_task(task_name, command, NODE_ID)
    result = None
    cleanup = None
    try:
        result = api.run_task(task)
    finally:
        cleanup = api.cleanup_task(task["id"], NODE_ID)
    if result.get("status") != "SUCCESS":
        raise RuntimeError("native2_readonly_task_failed")
    try:
        probe = json.loads(result.get("stdout") or "")
    except (ValueError, TypeError):
        raise RuntimeError("native2_probe_output_invalid")
    if probe.get("status") != "PASS" or probe.get("probe_side_effects") is not False:
        raise RuntimeError("native2_probe_assertion_failed")
    after = api.get_json("/api/nodes/" + NODE_ID)
    api.write_json("native2-readonly-probe.json", {"status":"PASS","authority":api.HUB,"node_before":{k:before.get(k) for k in ("id","hostname","ip","status","sync_status","deployment_mode","capabilities","agent_version")},"node_after":{k:after.get(k) for k in ("id","hostname","ip","status","sync_status","deployment_mode","capabilities","agent_version")},"node_remained_online_synced":after.get("status")=="online" and after.get("sync_status")=="synced","probe":probe,"task_id":task["id"],"execution_id":result["id"],"task_cleanup":cleanup,"product_state_changed":False})
    print(json.dumps({"status":"PASS","node_id":NODE_ID,"ip":after.get("ip"),"repair_agent_ancestor_sha_matches":probe.get("repair_agent_ancestor_sha_matches"),"docker_server_reachable":(probe.get("docker",{}).get("server") or {}).get("reachable"),"docker_image_count":len(probe.get("docker",{}).get("images",[])),"nsenter_available":probe.get("nsenter",{}).get("available"),"task_id":task["id"],"execution_id":result["id"],"task_deleted_get404":cleanup.get("get_after_delete_http")==404}))


if __name__ == "__main__":
    main()
