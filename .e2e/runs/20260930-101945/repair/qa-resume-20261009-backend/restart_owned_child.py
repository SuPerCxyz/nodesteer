#!/usr/bin/env python3
"""Restart only the ledger-verified owned child for credential re-enrollment."""
import hashlib
import json
import os
import re
import stat
import sys
import time
from pathlib import Path

import test_api as api
import run_ttl_recovery_retry as case

HERE = Path(__file__).resolve().parent
CHILD = case.CHILD
EXPECTED_STATUS = sys.argv[1] if len(sys.argv) > 1 else "online"
if EXPECTED_STATUS not in {"online", "maintenance", "disabled"}:
    raise SystemExit("unsupported_expected_status")


def main():
    c = case.claims()
    before = api.get("/api/nodes/" + CHILD)
    if before.get("id") != CHILD or before.get("agent_id") != c["agent_id"] or before.get("status") != EXPECTED_STATUS or before.get("sync_status") != "synced":
        raise RuntimeError("child_not_ready_for_real_reconnect")
    baseline = json.loads((HERE / "reenroll-case-baseline.json").read_text())
    current_oracle = HERE / "reenroll-reconnect-oracle.json"
    old_credential_sha = json.loads(current_oracle.read_text())["oracle"]["credential_sha256"] if current_oracle.exists() else baseline["child_identity"]["credential_sha256"]
    setup = json.loads((HERE.parent / "qa-resume-20261008-backend/setup/setup-summary.json").read_text())
    native4 = setup["target_native4"]["node_id"]

    source = case.guard(c) + r'''
import json,os,pwd,re,signal,stat,subprocess,time,uuid
from pathlib import Path
manifest=json.loads((D/"owner-manifest.json").read_text())
if manifest.get("directory")!=str(D) or manifest.get("node_id")!=C["node_id"] or manifest.get("agent_id")!=C["agent_id"]: raise RuntimeError("manifest_identity_mismatch")
if manifest.get("child",{}).get("pid")!=C["child"]["pid"] or manifest.get("child",{}).get("starttime")!=C["child"]["starttime"]: raise RuntimeError("manifest_child_claim_mismatch")
marker=D/"owner.marker"
if marker.is_symlink() or marker.read_text()!=manifest.get("owner_marker"): raise RuntimeError("owner_marker_mismatch")
controls=D/"controls.json"
if controls.is_symlink() or json.loads(controls.read_text())!={}: raise RuntimeError("controls_not_empty")
cfg=Path(manifest.get("config",{}).get("path",""))
binary=Path(manifest.get("binary",{}).get("copy_path",""))
if cfg.is_symlink() or not cfg.resolve().is_relative_to(D.resolve()) or binary.is_symlink() or not binary.resolve().is_relative_to(D.resolve()): raise RuntimeError("config_or_binary_path_escape")
if cfg.stat().st_uid!=C["directory"]["uid"] or stat.S_IMODE(cfg.stat().st_mode)!=0o600: raise RuntimeError("config_owner_mode_mismatch")
if binary.stat().st_uid!=C["directory"]["uid"] or sha(binary)!=manifest["binary"]["copy_sha256"] or sha(binary)!=C["child"]["exe_sha256"]: raise RuntimeError("binary_identity_mismatch")
lines=cfg.read_text().splitlines()
def scalar(key):
 rows=[x for x in lines if re.match(r"^\s*"+re.escape(key)+r"\s*:",x)]
 if len(rows)!=1: raise RuntimeError("config_scalar_missing_or_duplicate")
 value=json.loads(rows[0].split(":",1)[1].strip())
 if not isinstance(value,str): raise RuntimeError("config_scalar_not_string")
 return value
if scalar("agent_id")!=C["agent_id"] or scalar("hub_url")!="ws://"+C["proxy"]["ip"]+":"+str(C["proxy"]["port"])+"/": raise RuntimeError("config_identity_or_proxy_mismatch")
grant=scalar("registration_token")
if hashlib.sha256(grant.encode()).hexdigest()[:16]!=manifest.get("registration_token_fingerprint"): raise RuntimeError("registration_grant_fingerprint_mismatch")
db=D/"data"/"state.db"
import sqlite3
conn=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=3)
try: identity=dict(conn.execute("SELECT key,value FROM identity WHERE key IN ('node_id','agent_id','credential')").fetchall())
finally: conn.close()
if identity.get("node_id")!=C["node_id"] or identity.get("agent_id")!=C["agent_id"] or hashlib.sha256(identity.get("credential","").encode()).hexdigest()!=%r: raise RuntimeError("pre_restart_credential_or_identity_mismatch")
old=C["child"]; pid=old["pid"]
os.kill(pid,signal.SIGTERM)
deadline=time.monotonic()+8
while time.monotonic()<deadline:
 try:
  raw=Path("/proc",str(pid),"stat").read_text(); start=int(raw[raw.rfind(")")+2:].split()[19])
 except (FileNotFoundError,ProcessLookupError): break
 if start!=old["starttime"]: break
 time.sleep(.1)
else: raise RuntimeError("owned_child_did_not_exit_after_sigterm")
uid=old["uid"]
status=Path("/proc",str(C["supervisor"]["pid"]),"status").read_text().splitlines()
gid=int(next(x for x in status if x.startswith("Gid:")).split()[2])
log=D/"agent.log"
if log.is_symlink() or not log.resolve().is_relative_to(D.resolve()): raise RuntimeError("agent_log_path_escape")
def drop():
 os.setgroups([]); os.setgid(gid); os.setuid(uid)
with open(log,"ab") as stream:
 child=subprocess.Popen([str(binary),"--config",str(cfg)],cwd=str(D),stdin=subprocess.DEVNULL,stdout=stream,stderr=stream,close_fds=True,start_new_session=True,preexec_fn=drop,env={"PATH":"/usr/bin:/bin","HOME":str(D)})
time.sleep(.5)
raw=Path("/proc",str(child.pid),"stat").read_text(); start=int(raw[raw.rfind(")")+2:].split()[19])
status=Path("/proc",str(child.pid),"status").read_text().splitlines(); newuid=tuple(map(int,next(x for x in status if x.startswith("Uid:")).split()[1:3]))
exe=os.readlink("/proc/"+str(child.pid)+"/exe")
argv=[x.decode(errors="replace") for x in Path("/proc",str(child.pid),"cmdline").read_bytes().split(b"\0") if x]
if newuid!=(uid,uid) or sha(exe)!=old["exe_sha256"] or not any(x==str(D) or x.startswith(str(D)+os.sep) for x in argv): raise RuntimeError("new_child_identity_mismatch")
newrow={"pid":child.pid,"starttime":start,"uid":uid,"exe_path":exe,"exe_sha256":sha(exe),"argv_owner_path_match":True}
manifest["child"]=newrow
tmp=D/("owner-manifest.json.tmp-"+uuid.uuid4().hex)
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
with os.fdopen(fd,"wb") as f: f.write(json.dumps(manifest,separators=(",",":")).encode())
os.chown(tmp,uid,gid); os.chmod(tmp,0o600); tmp.replace(D/"owner-manifest.json")
raw=(D/"owner-manifest.json").read_bytes()
print(json.dumps({"status":"PASS","node_id":C["node_id"],"agent_id":C["agent_id"],"old_child":{"pid":pid,"starttime":old["starttime"],"uid":uid,"exe_sha256":old["exe_sha256"]},"new_child":newrow,"supervisor_unchanged":True,"controls_empty":json.loads(controls.read_text())=={},"config_sha256":sha(cfg),"old_credential_sha256":hashlib.sha256(identity["credential"].encode()).hexdigest(),"manifest_sha256":hashlib.sha256(raw).hexdigest(),"credential_value_emitted":False},separators=(",",":")))
''' % old_credential_sha
    result = api.native4_command("reenroll-guarded-child-restart-" + EXPECTED_STATUS, source, native4, timeout=60)
    value = result.get("output")
    if result.get("status") != "SUCCESS" or not isinstance(value, dict) or value.get("status") != "PASS":
        raise RuntimeError("guarded_child_restart_failed")
    if value.get("node_id") != CHILD or value.get("agent_id") != c["agent_id"] or value.get("controls_empty") is not True:
        raise RuntimeError("guarded_child_restart_result_mismatch")

    ledger_path = HERE / "owned-process-ledger.json"
    ledger = json.loads(ledger_path.read_text())
    if ledger.get("owner_manifest_sha256") != c["owner_manifest_sha256"] or ledger.get("child", {}).get("pid") != value["old_child"]["pid"] or ledger.get("supervisor", {}).get("pid") != c["supervisor"]["pid"]:
        raise RuntimeError("local_ledger_changed_during_restart")
    new = value["new_child"]
    ledger.update({"child": {**new, "uid_matches": True, "exe_sha_matches": True, "starttime_matches": True, "argv_owner_path_matches": True}, "owner_manifest_sha256": value["manifest_sha256"], "task_id": result["task_id"], "execution_id": result["execution_id"], "restart_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "build_manifest_agent_sha256": new["exe_sha256"]})
    api.write_json(ledger_path, ledger)
    api.write_json(HERE / ("reenroll-guarded-child-restart-" + EXPECTED_STATUS + ".json"), {"status":"PASS","expected_status":EXPECTED_STATUS,"restart":value,"task_id":result["task_id"],"execution_id":result["execution_id"],"source_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),"local_ledger_updated_immediately":True})
    print(json.dumps({"status":"PASS","old_pid":value["old_child"]["pid"],"new_pid":new["pid"],"new_starttime":new["starttime"],"manifest_sha256":value["manifest_sha256"],"task_id":result["task_id"],"execution_id":result["execution_id"]}))


if __name__ == "__main__":
    main()
