#!/usr/bin/env python3
"""Start the uniquely owned loopback HTTPS service after exact root/unit guards."""
import hashlib
import json
import os
import re
import socket
import stat
import sys
import time
from pathlib import Path

import api

HERE=Path(__file__).resolve().parent
PLAN=json.loads((HERE/"plan.json").read_text())
NODE_ID=PLAN["target_native2_node_id"]
SETUP=json.loads((HERE/"owner-root-created.json").read_text())
C=SETUP["result"]
ROOT=PLAN["remote_owned_root"]
MANIFEST_SHA=C["manifest_sha256"]
UNIT=PLAN["units"]["https_server"]
UNIT_SHA=C["server_unit"]["file_sha256"]


def main():
    node=api.get_json("/api/nodes/"+NODE_ID)
    if node.get("id")!=NODE_ID or node.get("status")!="online" or node.get("sync_status")!="synced":
        raise RuntimeError("native2_unhealthy_before_server_start")
    source=r'''import hashlib,json,os,socket,stat,subprocess,time,uuid
from pathlib import Path
C=json.loads(%r); D=Path(C["root"]); M=D/"owner-manifest.json"; U=Path("/etc/systemd/system")/%r
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C["root_dev"],C["root_ino"],0,0o700):raise RuntimeError("owned_root_identity_failed")
if hashlib.sha256(raw).hexdigest()!=%r or m.get("nonce")!=C["nonce"] or m.get("owner_marker")!= (D/"owner.marker").read_text():raise RuntimeError("owner_manifest_or_marker_guard_failed")
if U.is_symlink() or not U.is_file() or U.resolve()!=U or sha(U)!=%r:raise RuntimeError("server_unit_file_owner_guard_failed")
if (U.stat().st_uid,stat.S_IMODE(U.stat().st_mode))!=(0,0o644):raise RuntimeError("server_unit_file_mode_owner_failed")
if (D/"private"/"server-status.json").exists() or (D/"private"/"server-status.json").is_symlink():raise RuntimeError("server_status_must_be_absent_before_start")
run=lambda a:subprocess.run(a,text=True,capture_output=True,timeout=20)
def unit_state():
 p=run(["systemctl","show","--no-pager","--property=LoadState,ActiveState,MainPID,FragmentPath",%r])
 values={}
 for line in p.stdout.splitlines():
  if "=" in line:
   k,v=line.split("=",1);values[k]=v
 return p,values
before,bv=unit_state()
if before.returncode!=0 or bv.get("LoadState")!="loaded" or bv.get("ActiveState")!="inactive" or bv.get("MainPID")!="0" or bv.get("FragmentPath")!=str(U):raise RuntimeError("server_unit_state_or_fragment_guard_failed_before_load")
reload=run(["systemctl","daemon-reload"])
if reload.returncode:raise RuntimeError("systemd_daemon_reload_failed")
loaded,lv=unit_state()
if loaded.returncode or lv.get("LoadState")!="loaded" or lv.get("ActiveState")!="inactive" or lv.get("MainPID")!="0" or lv.get("FragmentPath")!=str(U):raise RuntimeError("server_unit_load_state_or_fragment_mismatch")
start=run(["systemctl","start",%r])
if start.returncode:raise RuntimeError("unique_server_unit_start_failed")
deadline=time.monotonic()+12; status_path=D/"private"/"server-status.json"; status=None
while time.monotonic()<deadline:
 try:
  if not status_path.is_symlink():status=json.loads(status_path.read_text());break
 except (OSError,ValueError):pass
 time.sleep(.1)
if not isinstance(status,dict) or status.get("bind")!="127.0.0.1" or not isinstance(status.get("port"),int) or not 1<=status["port"]<=65535:raise RuntimeError("loopback_server_status_invalid")
pid=int(run(["systemctl","show","--property=MainPID","--value",%r]).stdout.strip())
if pid<=1 or pid!=status.get("pid"):raise RuntimeError("server_mainpid_status_pid_mismatch")
rawstat=Path("/proc",str(pid),"stat").read_text();starttime=int(rawstat[rawstat.rfind(")")+2:].split()[19])
procstatus=Path("/proc",str(pid),"status").read_text().splitlines();uid=tuple(map(int,next(x for x in procstatus if x.startswith("Uid:")).split()[1:3]))
exe=os.readlink("/proc/"+str(pid)+"/exe");args=[x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(b"\0") if x]
cgroup=Path("/proc",str(pid),"cgroup").read_text().strip()
if uid!=(0,0) or "python3" not in exe or not any(x==str(D/"private"/"https_artifact_server.py") for x in args) or str(U.name) not in cgroup:raise RuntimeError("server_process_owner_argv_cgroup_guard_failed")
iphex=socket.inet_aton("127.0.0.1")[::-1].hex().upper();porthex=f"{status['port']:04X}";matches=[]
for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
 f=line.split()
 if f[1]==iphex+":"+porthex and f[3]=="0A":matches.append(f[9])
if not matches:raise RuntimeError("server_not_listening_on_exact_loopback_port")
server=m["units"]["https_server"];server.update({"pid":pid,"starttime":starttime,"uid":uid[0],"exe_path":exe,"exe_sha256":sha(exe),"argv_contains_exact_server_script":True,"cgroup":cgroup,"port":status["port"],"listen_loopback":True,"socket_inodes":matches,"active_state":"active"})
tmp=D/("owner-manifest.json.tmp-"+uuid.uuid4().hex);fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
with os.fdopen(fd,"wb") as f:f.write(json.dumps(m,separators=(",",":")).encode())
os.chown(tmp,0,0);os.chmod(tmp,0o600);tmp.replace(M);newraw=M.read_bytes()
print(json.dumps({"status":"PASS","root":str(D),"root_dev":st.st_dev,"root_ino":st.st_ino,"unit":str(U),"unit_sha256":sha(U),"server":{"pid":pid,"starttime":starttime,"uid":uid[0],"exe":exe,"exe_sha256":sha(exe),"cgroup":cgroup,"port":status["port"],"bind":"127.0.0.1","listen_socket_inodes":matches},"server_status_path":str(status_path),"server_status_mode":oct(stat.S_IMODE(status_path.stat().st_mode)),"server_status_uid":status_path.stat().st_uid,"initial_metrics":{"http_requests":status.get("http_requests",0),"tls_handshake_failures":status.get("tls_handshake_failures",0)},"manifest_sha256":hashlib.sha256(newraw).hexdigest(),"key_values_emitted":False},separators=(",",":")))
''' % (json.dumps({"root":ROOT,"root_dev":C["root_identity"]["dev"],"root_ino":C["root_identity"]["ino"],"nonce":PLAN["nonce"]},separators=(",",":")),UNIT,MANIFEST_SHA,UNIT_SHA,UNIT,UNIT,UNIT)
    command="python3 - <<'PY'\n"+source.rstrip()+"\nPY"
    task_name="qa-resume-20261009-host-tls-start-server-"+PLAN["nonce"]
    task=api.create_task(task_name,command,NODE_ID)
    result=None;cleanup=None
    try:result=api.run_task(task)
    finally:cleanup=api.cleanup_task(task["id"],NODE_ID)
    if result.get("status")!="SUCCESS":raise RuntimeError("loopback_server_start_task_failed")
    out=json.loads(result.get("stdout") or "")
    if out.get("status")!="PASS" or out.get("server",{}).get("bind")!="127.0.0.1" or out.get("server_status_uid")!=0:
        raise RuntimeError("loopback_server_start_assertion_failed")
    api.write_json("https-server-started.json",{"result":out,"task_id":task["id"],"execution_id":result["id"],"task_cleanup":cleanup,"source_sha256":hashlib.sha256(source.encode()).hexdigest()})
    print(json.dumps({"status":"PASS","nonce":PLAN["nonce"],"server_pid":out["server"]["pid"],"starttime":out["server"]["starttime"],"port":out["server"]["port"],"bind":out["server"]["bind"],"manifest_sha256":out["manifest_sha256"],"task_get404":cleanup.get("get_after_delete_http")==404}))


if __name__=="__main__":main()
