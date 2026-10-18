#!/usr/bin/env python3
"""Prove the exact loopback TLS server and CA before enrolling the child."""
import hashlib
import json
import re
import sys
from pathlib import Path

import api

HERE=Path(__file__).resolve().parent
PLAN=json.loads((HERE/"plan.json").read_text())
ROOT=PLAN["remote_owned_root"]
NODE=PLAN["target_native2_node_id"]
ROOT_RESULT=json.loads((HERE/"owner-root-created.json").read_text())["result"]
START_RESULT=json.loads((HERE/"https-server-started.json").read_text())["result"]

def main():
    node=api.get_json("/api/nodes/"+NODE)
    if node.get("status")!="online" or node.get("sync_status")!="synced":raise RuntimeError("native2_health_gate_failed")
    r=ROOT_RESULT;s=START_RESULT["server"]
    source=r'''import hashlib,json,os,socket,stat,subprocess,time
from pathlib import Path
C=json.loads(%r); D=Path(C["root"]); M=D/"owner-manifest.json";U=Path("/etc/systemd/system")/C["unit"]
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""):h.update(b)
 return h.hexdigest()
st=D.lstat();raw=M.read_bytes();m=json.loads(raw)
if D.is_symlink() or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C["dev"],C["ino"],0,0o700):raise RuntimeError("owner_root_guard_failed")
if hashlib.sha256(raw).hexdigest()!=C["manifest_sha"] or m.get("nonce")!=C["nonce"] or m.get("owner_marker")!=(D/"owner.marker").read_text():raise RuntimeError("owner_manifest_guard_failed")
owner=m["units"]["https_server"]
if owner.get("pid")!=C["pid"] or owner.get("starttime")!=C["starttime"] or owner.get("port")!=C["port"]:raise RuntimeError("server_manifest_claim_mismatch")
pid=C["pid"];p=Path("/proc",str(pid));rawstat=(p/"stat").read_text();start=int(rawstat[rawstat.rfind(")")+2:].split()[19]);uid=tuple(map(int,next(x for x in (p/"status").read_text().splitlines() if x.startswith("Uid:")).split()[1:3]));exe=os.readlink(str(p/"exe"));argv=[x.decode(errors="replace") for x in (p/"cmdline").read_bytes().split(b"\0") if x];cg=(p/"cgroup").read_text().strip()
if start!=C["starttime"] or uid!=(0,0) or sha(exe)!=owner["exe_sha256"] or str(D/"private"/"https_artifact_server.py") not in argv or U.name not in cg:raise RuntimeError("server_process_owner_guard_failed")
status_path=D/"private"/"server-status.json";status=json.loads(status_path.read_text())
if status_path.is_symlink() or status.get("pid")!=pid or status.get("port")!=C["port"] or status.get("bind")!="127.0.0.1":raise RuntimeError("server_status_owner_guard_failed")
ca=D/"private"/"ca.crt";ca_st=ca.stat()
if ca.is_symlink() or ca_st.st_uid!=0 or stat.S_IMODE(ca_st.st_mode)!=0o644:raise RuntimeError("ca_cert_owner_mode_failed")
before=json.loads(status_path.read_text());cmd=["/usr/bin/openssl","s_client","-brief","-verify_return_error","-verify_ip","127.0.0.1","-CAfile",str(ca),"-connect","127.0.0.1:"+str(C["port"])]
x=subprocess.run(cmd,input="",text=True,capture_output=True,timeout=10)
after=json.loads(status_path.read_text());out=(x.stdout+"\n"+x.stderr)
verified=x.returncode==0 and "Verification: OK" in out
if not verified:raise RuntimeError("loopback_tls_peer_verification_failed")
if before.get("http_requests",0)!=after.get("http_requests",0):raise RuntimeError("tls_preflight_unexpected_http_request")
print(json.dumps({"status":"PASS","verified_peer":verified,"openssl_returncode":x.returncode,"tls_version_present":"Protocol version" in out,"ca_cert_sha256":sha(ca),"server_cert_sha256":sha(D/"private"/"server.crt"),"server":{"pid":pid,"starttime":start,"port":C["port"],"bind":"127.0.0.1"},"http_requests_before":before.get("http_requests",0),"http_requests_after":after.get("http_requests",0),"tls_handshake_failures":after.get("tls_handshake_failures",0),"owner_manifest_sha256":hashlib.sha256(M.read_bytes()).hexdigest()},separators=(",",":")))
''' % json.dumps({"root":ROOT,"dev":r["root_identity"]["dev"],"ino":r["root_identity"]["ino"],"manifest_sha":START_RESULT["manifest_sha256"],"nonce":PLAN["nonce"],"unit":PLAN["units"]["https_server"],"pid":s["pid"],"starttime":s["starttime"],"port":s["port"]},separators=(",",":"))
    command="python3 - <<'PY'\n"+source.rstrip()+"\nPY"
    task=api.create_task("qa-resume-20261009-host-tls-probe-server-"+PLAN["nonce"],command,NODE)
    try:result=api.run_task(task)
    finally:cleanup=api.cleanup_task(task["id"],NODE)
    if result.get("status")!="SUCCESS":raise RuntimeError("tls_server_probe_task_failed")
    out=json.loads(result.get("stdout") or "")
    api.write_json("https-server-prechild-probe.json",{"status":out.get("status"),"result":out,"task_id":task["id"],"execution_id":result["id"],"cleanup":cleanup})
    print(json.dumps({"status":out.get("status"),"port":out["server"]["port"],"verified_peer":out["verified_peer"],"http_requests_before_after":[out["http_requests_before"],out["http_requests_after"]],"task_get404":cleanup.get("get_after_delete_http")==404}))

if __name__=="__main__":main()
