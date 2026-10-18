#!/usr/bin/env python3
"""Read exact TLS server Unit/root state without changing systemd or files."""
import json
import hashlib
import os
import stat
import subprocess
import uuid
from pathlib import Path

import api

HERE=Path(__file__).resolve().parent
PLAN=json.loads((HERE/"plan.json").read_text())
CREATED=json.loads((HERE/"owner-root-created.json").read_text())
NODE=PLAN["target_native2_node_id"]
ROOT=PLAN["remote_owned_root"]
UNIT=PLAN["units"]["https_server"]

def main():
    node=api.get_json("/api/nodes/"+NODE)
    if node.get("status")!="online" or node.get("sync_status")!="synced":raise RuntimeError("native2_health_gate_failed")
    r=CREATED["result"]
    source=r'''import hashlib,json,os,stat,subprocess
from pathlib import Path
D=Path(%r); U=%r; EXPECTED_ROOT=(%r,%r); EXPECTED_MANIFEST=%r; EXPECTED_UNIT_SHA=%r
def cmd(prop):
 p=subprocess.run(["systemctl","show","--no-pager","--property="+prop,"--value",U],text=True,capture_output=True,timeout=10)
 return {"returncode":p.returncode,"stdout":p.stdout.strip()}
st=D.lstat(); m=D/"owner-manifest.json";raw=m.read_bytes();man=json.loads(raw);unit=Path("/etc/systemd/system")/U;status=D/"private"/"server-status.json"
u=hashlib.sha256(unit.read_bytes()).hexdigest()
out={"status":"PASS","root":{"dev":st.st_dev,"ino":st.st_ino,"uid":st.st_uid,"mode":oct(stat.S_IMODE(st.st_mode)),"realpath":str(D.resolve()),"expected_dev_ino":list(EXPECTED_ROOT),"identity_matches":(st.st_dev,st.st_ino)==EXPECTED_ROOT},"manifest":{"sha256":hashlib.sha256(raw).hexdigest(),"expected_sha256":EXPECTED_MANIFEST,"identity_matches":hashlib.sha256(raw).hexdigest()==EXPECTED_MANIFEST and man.get("root")==str(D)},"unit":{"path":str(unit),"exists":unit.is_file() and not unit.is_symlink(),"sha256":u,"expected_sha256":EXPECTED_UNIT_SHA,"mode":oct(stat.S_IMODE(unit.stat().st_mode)),"uid":unit.stat().st_uid},"status_file_absent":not status.exists() and not status.is_symlink(),"systemctl":{"LoadState":cmd("LoadState"),"ActiveState":cmd("ActiveState"),"MainPID":cmd("MainPID"),"SubState":cmd("SubState"),"is_active":subprocess.run(["systemctl","is-active",U],text=True,capture_output=True,timeout=10).returncode}}
print(json.dumps(out,separators=(",",":")))
'''%(ROOT,UNIT,r["root_identity"]["dev"],r["root_identity"]["ino"],r["manifest_sha256"],r["server_unit"]["file_sha256"])
    command="python3 - <<'PY'\n"+source+"\nPY"
    task=api.create_task("qa-resume-20261009-host-tls-unit-state-probe-"+PLAN["nonce"],command,NODE)
    try:result=api.run_task(task)
    finally:cleanup=api.cleanup_task(task["id"],NODE)
    if result.get("status")!="SUCCESS":raise RuntimeError("unit_state_probe_task_failed")
    out=json.loads(result.get("stdout") or "")
    api.write_json("server-unit-state-prestart.json",{"result":out,"task_id":task["id"],"execution_id":result["id"],"cleanup":cleanup})
    print(json.dumps({"status":"PASS","LoadState":out["systemctl"]["LoadState"],"ActiveState":out["systemctl"]["ActiveState"],"MainPID":out["systemctl"]["MainPID"],"root_guard":out["root"]["identity_matches"],"manifest_guard":out["manifest"]["identity_matches"],"unit_guard":out["unit"]["sha256"]==out["unit"]["expected_sha256"],"task_get404":cleanup.get("get_after_delete_http")==404}))

if __name__=="__main__":main()
