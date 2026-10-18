#!/usr/bin/env python3
"""Run only the fresh valid Remote condition recovery; reuse seven existing negatives."""
import hashlib, json, time, uuid
from pathlib import Path
import test_api as api

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / "qa-resume-20261008-backend/cases"
CHILD = "15961ef7-81b4-43f3-80c8-1b6977ca2aea"
CASE = "BE-REMOTE-TTL-004"


def progress(stage, **extra):
    p = HERE / "progress.json"
    d = json.loads(p.read_text())
    d.update({"stage": stage, "actual_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **extra})
    api.write_json(p, d)


def claims():
    ledger = json.loads((HERE / "owned-process-ledger.json").read_text())
    build = json.loads((HERE.parent / "build-manifest.json").read_text())
    agent_sha = next(x["sha256"] for x in build["artifacts"] if x.get("name") == "nodesteer-agent-static")
    if ledger.get("status") != "PASS" or ledger.get("node_id") != CHILD or not ledger.get("controls_empty"):
        raise RuntimeError("current_owner_ledger_gate")
    if ledger["child"]["exe_sha256"] != agent_sha or not ledger["proxy"]["listening"] or not ledger["proxy"]["owned_by_supervisor"]:
        raise RuntimeError("current_build_or_proxy_gate")
    return {k: ledger[k] for k in ("node_id","agent_id","owner_dir","owner_manifest_sha256","directory","child","supervisor","proxy")}


def guard(c):
    return r'''import hashlib,json,os,stat
from pathlib import Path
C=json.loads(%r); D=Path(C["owner_dir"]); M=D/"owner-manifest.json"
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""): h.update(b)
 return h.hexdigest()
def verify_proc(p):
 pid=p["pid"]; raw=Path("/proc",str(pid),"stat").read_text(); start=int(raw[raw.rfind(")")+2:].split()[19])
 status=Path("/proc",str(pid),"status").read_text().splitlines(); uid=tuple(map(int,next(x for x in status if x.startswith("Uid:")).split()[1:3]))
 exe=os.readlink("/proc/"+str(pid)+"/exe"); argv=[x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(b"\0") if x]
 if start!=p["starttime"] or uid!=(p["uid"],p["uid"]) or sha(exe)!=p["exe_sha256"] or not any(x==str(D) or x.startswith(str(D)+os.sep) for x in argv): raise RuntimeError("owned_process_identity_failed")
st=D.lstat(); raw=M.read_bytes(); m=json.loads(raw)
if stat.S_ISLNK(st.st_mode) or D.resolve()!=D or (st.st_dev,st.st_ino,st.st_uid,stat.S_IMODE(st.st_mode))!=(C["directory"]["dev"],C["directory"]["ino"],C["directory"]["uid"],int(C["directory"]["mode"],8)): raise RuntimeError("owner_directory_identity_failed")
if hashlib.sha256(raw).hexdigest()!=C["owner_manifest_sha256"] or m.get("node_id")!=C["node_id"] or m.get("agent_id")!=C["agent_id"]: raise RuntimeError("owner_manifest_identity_failed")
verify_proc(C["child"]); verify_proc(C["supervisor"])
''' % json.dumps(c, separators=(",",":"))


def marker_source(c, folder, filename, content, folder_exists):
    root = Path(c["owner_dir"]) / "data" / "qa-be-remote-ttl"
    return guard(c) + r'''
root=D/"data"/"qa-be-remote-ttl"
if root.is_symlink() or not root.resolve().is_relative_to(D.resolve()): raise RuntimeError("owned_test_root_escape")
root.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=Path(%r)
if folder.parent.resolve()!=root.resolve(): raise RuntimeError("owned_case_parent_mismatch")
folder.mkdir(mode=0o700,parents=False,exist_ok=%r)
if folder.is_symlink() or not folder.resolve().is_relative_to(root.resolve()) or folder.stat().st_uid!=C["directory"]["uid"]: raise RuntimeError("owned_case_directory_escape")
p=folder/%r; fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
with os.fdopen(fd,"wb") as f: f.write(%r.encode())
st=p.stat()
print(json.dumps({"status":"PASS","path":str(p),"realpath":str(p.resolve()),"exists":p.is_file(),"sha256":sha(p),"mode":oct(stat.S_IMODE(st.st_mode)),"uid":st.st_uid},separators=(",",":")))
''' % (str(folder), folder_exists, filename, content)


def file_read_source(c, path):
    body=guard(c)
    body += r'''
root=D/"data"/"qa-be-remote-ttl"; p=Path(%r)
if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()): raise RuntimeError("owned_file_path_escape")
st=p.stat(); b=p.read_bytes()
if st.st_uid!=C["directory"]["uid"] or stat.S_IMODE(st.st_mode)!=0o600: raise RuntimeError("owned_file_mode_owner_mismatch")
print(json.dumps({"exists":p.is_file(),"path":str(p),"realpath":str(p.resolve()),"text":b.decode(),"sha256":hashlib.sha256(b).hexdigest(),"mode":oct(stat.S_IMODE(st.st_mode)),"uid":st.st_uid},separators=(",",":")))
''' % path
    return body


def control_source(c, rule, clear=False):
    body = guard(c) + 'c=D/"controls.json"\n'
    if clear:
        body += r'''if json.loads(c.read_text())!={"remote_override":%r}: raise RuntimeError("exact_control_clear_guard")
''' % rule
    else:
        body += 'if json.loads(c.read_text())!={}: raise RuntimeError("controls_not_empty")\n'
    if clear:
        body += 't=c.with_suffix(".json.tmp"); fd=os.open(t,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)\nwith os.fdopen(fd,"w") as f: f.write("{}")\nos.chown(t,65534,65534); os.chmod(t,0o600); t.replace(c)\n'
    else:
        body += r'''t=c.with_suffix(".json.tmp"); fd=os.open(t,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0),0o600)
with os.fdopen(fd,"w") as f: f.write(json.dumps({"remote_override":%r},separators=(",",":")))
os.chown(t,65534,65534); os.chmod(t,0o600); t.replace(c)
''' % rule
    body += 'st=c.stat()\nprint(json.dumps({"status":"PASS","controls_empty":json.loads(c.read_text())=={},"sha256":sha(c),"mode":oct(stat.S_IMODE(st.st_mode)),"uid":st.st_uid},separators=(",",":")))\n'
    return body


def main():
    cp=json.loads((OLD/"BE-REMOTE-TTL-004-checkpoint.json").read_text())
    negatives=cp.get("completed_negative_cases",[])
    if cp.get("step")!="negative-cases-PASS" or len(negatives)!=7 or not all(x.get("execution_status")=="BLOCKED" and x.get("controls_cleared") for x in negatives):
        raise RuntimeError("previous_negative_checkpoint_not_ready")
    c=claims()
    node=api.get("/api/nodes/"+CHILD)
    if node.get("status")!="online" or node.get("sync_status")!="synced": raise RuntimeError("owned_child_not_online_synced")
    setup=json.loads((HERE.parent/"qa-resume-20261008-backend/setup/setup-summary.json").read_text())
    native4=setup["target_native4"]["node_id"]
    progress("ttl-recovery-running",status="RUNNING",next_action="Fresh history SUCCESS then one valid Remote condition recovery; do not rerun seven negatives.")
    history_evidence=HERE/"BE-REMOTE-TTL-004-history-run-execution.json"
    suffix=None
    if history_evidence.exists() and time.time()-history_evidence.stat().st_mtime<=45:
        hist=json.loads(history_evidence.read_text())
        history=api.get("/api/tasks/"+hist["task_id"])
        if history.get("id")!=hist.get("task_id") or not history.get("name","").startswith("qa-resume-20261009-remote-history-") or history.get("target",{}).get("node_ids")!=[CHILD] or history.get("retry")!=0 or history.get("schedule"):
            raise RuntimeError("existing_history_task_identity_mismatch")
        history_run=api.get("/api/executions/"+hist["execution_id"])
        if history_run.get("status")!="SUCCESS" or history_run.get("node_id")!=CHILD or history_run.get("task_id")!=history["id"]: raise RuntimeError("existing_history_execution_mismatch")
        history_output=json.loads(history_run.get("stdout") or "{}")
    else:
        suffix=uuid.uuid4().hex[:8]
        folder=str(Path(c["owner_dir"])/"data"/"qa-be-remote-ttl"/suffix)
        history_src=marker_source(c,folder,"history.txt","history-success",False)
        history=api.create_task("qa-resume-20261009-remote-history-"+suffix,api.api.make_python_command(history_src),CHILD,timeout=30)
        history_run=api.run_task(history,CASE+"-history-run",90)
        if history_run.get("status")!="SUCCESS": raise RuntimeError("fresh_history_failed")
        history_output=json.loads(history_run.get("stdout") or "{}")
    history_marker=history_output.get("path")
    folder=str(Path(history_marker).parent)
    suffix=Path(folder).name
    root=(Path(c["owner_dir"])/"data"/"qa-be-remote-ttl").resolve()
    if not history_marker or not Path(history_marker).resolve().is_relative_to(root): raise RuntimeError("existing_history_marker_path_escape")
    history_oracle=api.native4_command(CASE+"-history-file-oracle",file_read_source(c,history_marker),native4,45)
    if history_oracle.get("status")!="SUCCESS" or history_oracle["output"].get("sha256")!=hashlib.sha256(b"history-success").hexdigest() or history_oracle["output"].get("uid")!=65534:
        raise RuntimeError("history_marker_oracle_failed")
    condition={"type":"remote","remote":{"node_id":CHILD,"property":"last_execution","task_id":history["id"],"operator":"==","value":"SUCCESS"}}
    recovery_marker=str(Path(folder)/"recovery-success.txt")
    recovery_src=marker_source(c,folder,"recovery-success.txt","remote-recovery-success",True)
    task=api.create_task("qa-resume-20261009-remote-recovery-"+suffix,api.api.make_python_command(recovery_src),CHILD,condition,45)
    rule={"target_node_id":CHILD,"property":"last_execution","task_id":history["id"],"observe_only":True,"delay_ms":500}
    arm=api.native4_command(CASE+"-arm-observe-only-delay",control_source(c,rule),native4,20)
    if arm.get("status")!="SUCCESS" or arm.get("output",{}).get("status")!="PASS": raise RuntimeError("delay_arm_failed")
    recovery=None; events=None; cleared=None
    try:
        recovery=api.run_task(task,CASE+"-recovery-run",90)
    finally:
        event_src=guard(c)+r'''
rows=[]
for line in (D/"proxy-events.jsonl").read_text().splitlines()[-1200:]:
 try:
  x=json.loads(line)
  if x.get("event") in {"remote_request","remote_override","remote_delivery_delay"} and (x.get("task_id")==%r or x.get("request",{}).get("task_id")==%r): rows.append(x)
 except Exception: pass
print(json.dumps(rows[-20:],separators=(",",":")))
''' % (history["id"],history["id"])
        try:
            events=api.native4_command(CASE+"-recovery-request-response-events",event_src,native4,30)
        finally:
            cleared=api.native4_command(CASE+"-clear-observe-only-delay",control_source(c,rule,True),native4,20)
    if not cleared or cleared.get("status")!="SUCCESS" or not cleared.get("output",{}).get("controls_empty"): raise RuntimeError("delay_clear_failed")
    if not recovery or recovery.get("status")!="SUCCESS":
        api.write_json(HERE/"ttl-recovery-positive-status.json",{"task_id":task["id"],"execution_id":(recovery or {}).get("id"),"status":(recovery or {}).get("status"),"block_reason":(recovery or {}).get("block_reason"),"proxy_controls_cleared":True})
        raise RuntimeError("valid_remote_recovery_failed")
    rows=events.get("output") if events else None
    if not isinstance(rows,list): raise RuntimeError("proxy_event_oracle_missing")
    req=next((x for x in reversed(rows) if x.get("event")=="remote_request"),None)
    override=next((x for x in reversed(rows) if x.get("event")=="remote_override"),None)
    delay=next((x for x in reversed(rows) if x.get("event")=="remote_delivery_delay"),None)
    if not req or not override or not delay or not (req.get("id")==override.get("id")==delay.get("id")): raise RuntimeError("request_response_correlation_failed")
    if override.get("before")!=override.get("after") or override.get("before",{}).get("value")!="SUCCESS" or delay.get("delay_ms")!=500: raise RuntimeError("payload_delay_oracle_failed")
    file_oracle=api.native4_command(CASE+"-recovery-file-oracle",file_read_source(c,recovery_marker),native4,45)
    if file_oracle.get("status")!="SUCCESS" or file_oracle["output"].get("sha256")!=hashlib.sha256(b"remote-recovery-success").hexdigest() or file_oracle["output"].get("uid")!=65534: raise RuntimeError("recovery_marker_oracle_failed")
    journal_src=guard(c)+r'''
import sqlite3
db=D/"data"/"state.db"; conn=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=3); conn.row_factory=sqlite3.Row
rows=[dict(x) for x in conn.execute("SELECT id,task_id,node_id,status,exit_code,synced,offline,block_reason FROM executions WHERE id=?",(%r,)).fetchall()]
print(json.dumps(rows,separators=(",",":")))
''' % recovery["id"]
    journal=api.native4_command(CASE+"-recovery-local-journal",journal_src,native4,45)
    jr=journal.get("output") if journal else None
    if journal.get("status")!="SUCCESS" or not isinstance(jr,list) or len(jr)!=1 or jr[0].get("status")!="SUCCESS" or not jr[0].get("synced"): raise RuntimeError("local_journal_oracle_failed")
    result={"case_id":CASE,"status":"PASS_FOR_CONFIRMED_REMOTE_TTL_PATH","source_checkpoint":str(OLD/"BE-REMOTE-TTL-004-checkpoint.json"),"source_step":cp["step"],"negative_cases_reused_without_rerun":[{"id":x["id"],"task_id":x["task_id"],"execution_id":x["execution_id"],"status":x["execution_status"],"block_reason":x["block_reason"],"controls_cleared":x["controls_cleared"]} for x in negatives],"ui_blocked_case_evidence":"ttl-blocked-execution-ui.json","child_node_id":CHILD,"history":{"task_id":history["id"],"execution_id":history_run["id"],"status":history_run["status"],"marker_path":str(Path(folder)/"history.txt"),"marker_sha256":history_oracle["output"]["sha256"]},"recovery":{"task_id":task["id"],"execution_id":recovery["id"],"status":recovery["status"],"marker_path":str(Path(folder)/"recovery-success.txt"),"marker_sha256":file_oracle["output"]["sha256"],"marker_uid":file_oracle["output"]["uid"],"marker_mode":file_oracle["output"]["mode"],"local_journal":jr[0]},"remote_request_response":{"request_id":req["id"],"request":req,"override":override,"delivery_delay":delay,"payload_unchanged":override["before"]==override["after"],"clock_skew_observation":cp.get("clock_skew_observation")},"controls_cleared":cleared["output"],"ui_success_pending":True,"cleanup_task_ids":[history["id"],task["id"]],"failed_harness_attempt":"ttl-recovery-attempt-02-harness-error.json","source_sha256":{"test_api.py":hashlib.sha256((HERE/"test_api.py").read_bytes()).hexdigest(),"run_ttl_recovery_retry.py":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}
    api.write_json(HERE/"ttl-recovery-summary.json",result)
    progress("ttl-recovery-business-PASS",status="PASS_FOR_CONFIRMED_REMOTE_TTL_PATH",next_action="Verify recovery SUCCESS in agent-browser; then precise cleanup of all TTL-owned Tasks and markers.",ttl_recovery_summary="ttl-recovery-summary.json",recovery_task_id=task["id"],recovery_execution_id=recovery["id"],history_task_id=history["id"])
    print(json.dumps({"status":"PASS","recovery_execution_id":recovery["id"],"history_execution_id":history_run["id"],"request_id":req["id"],"marker":True,"journal":True,"controls_empty":True}))


if __name__=="__main__":
    main()

