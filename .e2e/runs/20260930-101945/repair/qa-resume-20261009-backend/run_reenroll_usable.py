#!/usr/bin/env python3
"""Prove the reenrolled owned child can run and persist one successful task."""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

import test_api as api
import run_ttl_recovery_retry as case

HERE = Path(__file__).resolve().parent
CHILD = case.CHILD


def main():
    c = case.claims()
    node = api.get("/api/nodes/" + CHILD)
    if node.get("status") != "online" or node.get("sync_status") != "synced" or node.get("labels") != {}:
        raise RuntimeError("reenrolled_child_not_ready")
    setup = json.loads((HERE.parent / "qa-resume-20261008-backend/setup/setup-summary.json").read_text())
    native4 = setup["target_native4"]["node_id"]
    nonce = uuid.uuid4().hex[:12]
    filename = "qa-be-reenroll-" + nonce + ".txt"
    content = "reenrolled-child-success-" + nonce
    path = str(Path(c["owner_dir"]) / "data" / filename)
    source = case.guard(c) + r'''
from pathlib import Path
import os,stat
data=D/"data"
if data.is_symlink() or not data.resolve().is_relative_to(D.resolve()): raise RuntimeError("owned_data_path_escape")
st=data.stat()
if st.st_uid!=C["directory"]["uid"] or stat.S_IMODE(st.st_mode)!=0o700: raise RuntimeError("owned_data_owner_mode_mismatch")
p=data/%r
if p.exists() or p.is_symlink(): raise RuntimeError("marker_already_exists")
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
with os.fdopen(fd,"wb") as f: f.write(%r.encode())
st=p.stat()
print(json.dumps({"status":"PASS","path":str(p),"realpath":str(p.resolve()),"sha256":sha(p),"uid":st.st_uid,"mode":oct(stat.S_IMODE(st.st_mode))},separators=(",",":")))
''' % (filename, content)
    task = api.create_task("qa-resume-20261009-reenroll-usable-" + nonce, api.api.make_python_command(source), CHILD, timeout=45)
    result = api.run_task(task, "reenroll-usable-run", 90)
    if result.get("status") != "SUCCESS":
        raise RuntimeError("reenrolled_child_task_failed")
    output = json.loads(result.get("stdout") or "{}")
    if output.get("status") != "PASS" or output.get("path") != path or output.get("uid") != c["directory"]["uid"]:
        raise RuntimeError("reenrolled_child_task_marker_result_mismatch")

    oracle_source = case.guard(c) + r'''
from pathlib import Path
import stat
p=Path(%r); data=D/"data"
if p.is_symlink() or p.parent.resolve()!=data.resolve() or p.name!=%r: raise RuntimeError("marker_oracle_path_escape")
st=p.stat(); b=p.read_bytes()
if st.st_uid!=C["directory"]["uid"] or stat.S_IMODE(st.st_mode)!=0o600 or sha(p)!=%r: raise RuntimeError("marker_oracle_content_owner_mode_mismatch")
print(json.dumps({"status":"PASS","exists":p.is_file(),"path":str(p),"realpath":str(p.resolve()),"sha256":sha(p),"uid":st.st_uid,"mode":oct(stat.S_IMODE(st.st_mode))},separators=(",",":")))
''' % (path, filename, hashlib.sha256(content.encode()).hexdigest())
    marker = api.native4_command("reenroll-usable-marker-oracle", oracle_source, native4, 45)
    if marker.get("status") != "SUCCESS" or marker.get("output", {}).get("status") != "PASS":
        raise RuntimeError("reenrolled_child_marker_oracle_failed")
    journal_source = case.guard(c) + r'''
import sqlite3
conn=sqlite3.connect("file:"+str(D/"data"/"state.db")+"?mode=ro",uri=True,timeout=3); conn.row_factory=sqlite3.Row
try: rows=[dict(x) for x in conn.execute("SELECT id,task_id,node_id,status,exit_code,synced,offline,block_reason FROM executions WHERE id=?",(%r,)).fetchall()]
finally: conn.close()
print(json.dumps(rows,separators=(",",":")))
''' % result["id"]
    journal = api.native4_command("reenroll-usable-local-journal-oracle", journal_source, native4, 45)
    rows = journal.get("output") if journal else None
    if journal.get("status") != "SUCCESS" or not isinstance(rows, list) or len(rows) != 1 or rows[0].get("status") != "SUCCESS" or not rows[0].get("synced") or rows[0].get("node_id") != CHILD:
        raise RuntimeError("reenrolled_child_local_journal_oracle_failed")
    summary = {"status":"PASS","case_id":"BE-REENROLL-STATUS-003","task_id":task["id"],"execution_id":result["id"],"node_id":CHILD,"task_status":result["status"],"marker_path":path,"marker_sha256":marker["output"]["sha256"],"marker_uid":marker["output"]["uid"],"marker_mode":marker["output"]["mode"],"local_journal":rows[0],"marker_oracle_task_id":marker["task_id"],"marker_oracle_execution_id":marker["execution_id"],"journal_oracle_task_id":journal["task_id"],"journal_oracle_execution_id":journal["execution_id"],"cleanup_pending":True}
    api.write_json(HERE / "reenroll-usable-summary.json", summary)
    api.write_json(HERE / "reenroll-usable-task.json", {"id":task["id"],"name":task["name"],"target_node_id":CHILD,"retry":0,"schedule_present":False,"command_sha256":hashlib.sha256(source.encode()).hexdigest()})
    print(json.dumps({"status":"PASS","task_id":task["id"],"execution_id":result["id"],"marker":True,"journal_synced":True,"cleanup_pending":True}))


if __name__ == "__main__":
    main()
