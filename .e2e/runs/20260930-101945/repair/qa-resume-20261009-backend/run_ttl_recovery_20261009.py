#!/usr/bin/env python3
"""Run only the fresh valid Remote condition recovery group."""
import hashlib
import json
import os
import time
import urllib.parse
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD_BACKEND = HERE.parent / "qa-resume-20261008-backend"
import run_env01_05 as api

api.PRIVATE = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json")
api.OUT = HERE
TOKEN = api.read_private()
CHILD = "15961ef7-81b4-43f3-80c8-1b6977ca2aea"
NATIVE4 = "03cb50cc-eab6-4afd-bbe0-f73c4e326773"
OWNER_DIR = "/tmp/qa-resume-20261008-child-ciivjupc"
MANIFEST_SHA = "7a5bf21f70ec2610527f054809f40fbc17bb5dcbfd24f8af804237bc811f7cfc"
AGENT_ID = "agent-ab15a133-73cd-4025-8c97-f2ff37e000f5"
TTL_ROOT = OWNER_DIR + "/data/qa-be-remote-ttl"

CASE = "BE-REMOTE-TTL-004"
SOURCE_HASHES = {
    "backend_cases.py_reviewed_not_imported": hashlib.sha256((OLD_BACKEND / "cases/backend_cases.py").read_bytes()).hexdigest(),
    "run_env01_05.py": hashlib.sha256((OLD_BACKEND / "run_env01_05.py").read_bytes()).hexdigest(),
    "run_remote_valid_control_delayed.py": hashlib.sha256((OLD_BACKEND / "cases/run_remote_valid_control_delayed.py").read_bytes()).hexdigest(),
}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)


def progress(stage, **extra):
    path = HERE / "progress.json"
    data = json.loads(path.read_text())
    data.update({"stage": stage, "actual_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **extra})
    write_json(path, data)


def native4_command(label, source, timeout=60):
    command = api.make_python_command(source)
    if h.TOKEN in command:
        raise RuntimeError("native4_command_contains_bearer")
    task = h.create_task(label + "-" + uuid.uuid4().hex[:8], command, node_id=h.NATIVE4, timeout=timeout)
    result = None
    try:
        result = h.run_task(task, label, expected_status="SUCCESS", timeout=timeout + 15)
        try:
            output = json.loads(result.get("stdout") or "")
        except (ValueError, TypeError):
            output = None
        return {"task_id": task["id"], "execution_id": result["id"], "status": result["status"], "output": output}
    finally:
        if task.get("id"):
            cleaned = h.delete_resource("task", task["id"], label + "-cleanup")
            if not cleaned:
                raise RuntimeError("native4_temporary_task_cleanup_failed")


def owner_guarded_marker_source(folder, filename, text):
    return '''import hashlib,json,os,stat
from pathlib import Path
D=Path(%r); M=D/"owner-manifest.json"
raw=M.read_bytes(); m=json.loads(raw)
if hashlib.sha256(raw).hexdigest()!=%r or m.get("node_id")!=%r or m.get("agent_id")!=%r: raise RuntimeError("owner_guard_failed")
root=D/"data"/"qa-be-remote-ttl"; root.mkdir(mode=0o700,parents=True,exist_ok=True)
folder=Path(%r)
folder.mkdir(mode=0o700,parents=True,exist_ok=False)
p=folder/%r; p.write_text(%r); os.chmod(p,0o600)
st=p.stat()
print(json.dumps({"status":"PASS","path":str(p),"exists":p.is_file(),"text_sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"mode":oct(stat.S_IMODE(st.st_mode)),"uid":st.st_uid},separators=(",",":")))
''' % (h.OWNED_DIR, h.MANIFEST_SHA, h.CHILD, "agent-ab15a133-73cd-4025-8c97-f2ff37e000f5", folder, filename, text)


def main():
    source_checkpoint = OLD_BACKEND / "cases/BE-REMOTE-TTL-004-checkpoint.json"
    cp = json.loads(source_checkpoint.read_text())
    negatives = cp.get("completed_negative_cases", [])
    if cp.get("step") != "negative-cases-PASS" or len(negatives) != 7 or not all(x.get("execution_status") == "BLOCKED" and x.get("controls_cleared") for x in negatives):
        raise RuntimeError("prior_negative_checkpoint_not_ready_do_not_rerun")
    child = h.api_get("/api/nodes/" + h.url(h.CHILD))
    if child.get("status") != "online" or child.get("sync_status") != "synced":
        raise RuntimeError("owned_child_not_online_synced")
    progress("ttl-recovery-running", status="RUNNING", next_action="Create fresh history SUCCESS, run valid Remote recovery with unchanged response and 500ms observe-only delay.", source_sha256=SOURCE_HASHES, recovery_started=True)

    suffix = uuid.uuid4().hex[:8]
    root = valid.ROOT + "/" + suffix
    history_file = root + "/history.txt"
    marker = root + "/recovery-success.txt"
    history = h.create_task("qa-resume-20261009-remote-history-" + suffix, api.make_python_command(owner_guarded_marker_source(root, "history.txt", "history-success")), timeout=30)
    if history.get("target", {}).get("node_ids") != [h.CHILD] or history.get("retry") != 0 or history.get("schedule"):
        raise RuntimeError("history_task_definition_guard_failed")
    history_run = h.run_task(history, CASE + "-history-run", expected_status="SUCCESS", timeout=90)
    history_file_result = native4_command(CASE + "-history-file-oracle", r'''import hashlib,json,os,stat
from pathlib import Path
D=Path("/tmp/qa-resume-20261008-child-ciivjupc"); M=D/"owner-manifest.json"; raw=M.read_bytes(); m=json.loads(raw)
if hashlib.sha256(raw).hexdigest()!="7a5bf21f70ec2610527f054809f40fbc17bb5dcbfd24f8af804237bc811f7cfc" or m.get("node_id")!="15961ef7-81b4-43f3-80c8-1b6977ca2aea": raise RuntimeError("owner_guard_failed")
p=Path(%r); b=p.read_bytes(); st=p.stat()
print(json.dumps({"exists":p.is_file(),"text":b.decode(),"sha256":hashlib.sha256(b).hexdigest(),"mode":oct(stat.S_IMODE(st.st_mode)),"uid":st.st_uid},separators=(",",":")))
''' % history_file)
    if history_run.get("status") != "SUCCESS" or not history_file_result.get("output", {}).get("exists") or history_file_result["output"].get("text") != "history-success":
        raise RuntimeError("fresh_history_business_effect_failed")

    condition = {"type": "remote", "remote": {"node_id": h.CHILD, "property": "last_execution", "task_id": history["id"], "operator": "==", "value": "SUCCESS"}}
    task = h.create_task("qa-resume-20261009-remote-recovery-" + suffix, api.make_python_command(owner_guarded_marker_source(root, "recovery-success.txt", "remote-recovery-success")), condition=condition, timeout=45)
    if task.get("target", {}).get("node_ids") != [h.CHILD] or task.get("retry") != 0 or task.get("schedule") or task.get("condition") != condition:
        raise RuntimeError("recovery_task_definition_guard_failed")
    rule = {"target_node_id": h.CHILD, "property": "last_execution", "task_id": history["id"], "observe_only": True, "delay_ms": 500}
    arm = native4_command(CASE + "-arm-observe-only-delay", valid.controls_source(rule), timeout=20)
    if arm.get("status") != "SUCCESS" or arm.get("output", {}).get("status") != "PASS":
        raise RuntimeError("observe_only_delay_arm_failed")

    recovery_run = None
    event_result = None
    clear = None
    try:
        recovery_run = h.run_task(task, CASE + "-recovery-run", expected_status="SUCCESS", timeout=90)
    finally:
        events_source = r'''import json
from pathlib import Path
rows=[]
for line in Path("/tmp/qa-resume-20261008-child-ciivjupc/proxy-events.jsonl").read_text().splitlines()[-1200:]:
 try:
  x=json.loads(line)
  if x.get("event") in {"remote_request","remote_override","remote_delivery_delay"} and (x.get("task_id")==%r or x.get("request",{}).get("task_id")==%r): rows.append(x)
 except Exception: pass
print(json.dumps(rows[-20:],separators=(",",":")))
''' % (history["id"], history["id"])
        event_result = native4_command(CASE + "-recovery-request-response-events", events_source, timeout=30)
        clear = native4_command(CASE + "-clear-observe-only-delay", valid.clear_source(rule), timeout=20)
    if not clear or clear.get("status") != "SUCCESS" or clear.get("output", {}).get("status") != "PASS":
        raise RuntimeError("observe_only_delay_clear_failed")
    if not recovery_run or recovery_run.get("status") != "SUCCESS":
        raise RuntimeError("remote_recovery_execution_not_success")
    events = event_result.get("output") if event_result else []
    if not isinstance(events, list):
        raise RuntimeError("remote_event_oracle_missing")
    request_event = next((x for x in reversed(events) if x.get("event") == "remote_request"), None)
    override_event = next((x for x in reversed(events) if x.get("event") == "remote_override"), None)
    delay_event = next((x for x in reversed(events) if x.get("event") == "remote_delivery_delay"), None)
    if not request_event or not override_event or not delay_event:
        raise RuntimeError("remote_request_response_correlation_missing")
    if not (request_event.get("id") == override_event.get("id") == delay_event.get("id")):
        raise RuntimeError("remote_request_id_mismatch")
    if override_event.get("before") != override_event.get("after") or override_event.get("before", {}).get("value") != "SUCCESS":
        raise RuntimeError("remote_payload_was_mutated")
    if delay_event.get("delay_ms") != 500 or not clear.get("output", {}).get("controls_empty"):
        raise RuntimeError("remote_delay_or_cleanup_oracle_failed")

    recovery_file_result = native4_command(CASE + "-recovery-file-oracle", r'''import hashlib,json,os,stat
from pathlib import Path
D=Path("/tmp/qa-resume-20261008-child-ciivjupc"); M=D/"owner-manifest.json"; raw=M.read_bytes(); m=json.loads(raw)
if hashlib.sha256(raw).hexdigest()!="7a5bf21f70ec2610527f054809f40fbc17bb5dcbfd24f8af804237bc811f7cfc" or m.get("node_id")!="15961ef7-81b4-43f3-80c8-1b6977ca2aea": raise RuntimeError("owner_guard_failed")
p=Path(%r); b=p.read_bytes(); st=p.stat()
print(json.dumps({"exists":p.is_file(),"text":b.decode(),"sha256":hashlib.sha256(b).hexdigest(),"mode":oct(stat.S_IMODE(st.st_mode)),"uid":st.st_uid},separators=(",",":")))
''' % marker)
    if recovery_file_result.get("output", {}).get("text") != "remote-recovery-success":
        raise RuntimeError("remote_recovery_marker_missing")
    journal = native4_command(CASE + "-recovery-local-journal", r'''import json,sqlite3
from pathlib import Path
D=Path("/tmp/qa-resume-20261008-child-ciivjupc"); M=D/"owner-manifest.json"; import hashlib
if hashlib.sha256(M.read_bytes()).hexdigest()!="7a5bf21f70ec2610527f054809f40fbc17bb5dcbfd24f8af804237bc811f7cfc": raise RuntimeError("owner_manifest_sha_mismatch")
db=D/"data"/"state.db"; c=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=3);c.row_factory=sqlite3.Row
rows=[dict(x) for x in c.execute("SELECT id,task_id,node_id,status,exit_code,synced,offline,block_reason FROM executions WHERE id=?",(%r,)).fetchall()]
print(json.dumps(rows,separators=(",",":")))
''' % recovery_run["id"])
    journal_rows = journal.get("output") if journal else None
    if not isinstance(journal_rows, list) or len(journal_rows) != 1 or journal_rows[0].get("status") != "SUCCESS" or not journal_rows[0].get("synced"):
        raise RuntimeError("remote_recovery_journal_mismatch")

    result = {
        "case_id": CASE,
        "status": "PASS_FOR_CONFIRMED_REMOTE_TTL_PATH",
        "source_checkpoint": str(source_checkpoint),
        "source_step": cp.get("step"),
        "negative_cases_reused_without_rerun": [{"id": x["id"], "execution_id": x["execution_id"], "status": x["execution_status"], "block_reason": x["block_reason"], "controls_cleared": x["controls_cleared"]} for x in negatives],
        "ui_blocked_case_evidence": "ttl-blocked-execution-ui.json",
        "child_node_id": h.CHILD,
        "history": {"task_id": history["id"], "execution_id": history_run["id"], "status": history_run["status"], "marker_path": history_file, "marker_sha256": history_file_result["output"].get("sha256")},
        "recovery": {"task_id": task["id"], "execution_id": recovery_run["id"], "status": recovery_run["status"], "marker_path": marker, "marker_sha256": recovery_file_result["output"].get("sha256"), "marker_mode": recovery_file_result["output"].get("mode"), "marker_uid": recovery_file_result["output"].get("uid"), "journal": journal_rows[0]},
        "remote_request_response": {"request_id": request_event.get("id"), "remote_request": request_event, "override": override_event, "delay": delay_event, "payload_unchanged": override_event.get("before") == override_event.get("after"), "delay_ms": delay_event.get("delay_ms"), "clock_skew_observation": cp.get("clock_skew_observation")},
        "controls_cleared": clear.get("output"),
        "ui_success_evidence_pending": True,
        "task_ids_for_precise_cleanup": [history["id"], task["id"]],
        "source_sha256": SOURCE_HASHES,
        "evidence_directory": str(HERE),
    }
    write_json(HERE / "ttl-recovery-summary.json", result)
    write_json(HERE / "ttl-recovery-result.json", {
        "history_task_definition": str(HERE / ("qa-resume-20261009-remote-history-" + suffix + "-definition.json")),
        "recovery_task_definition": str(HERE / ("qa-resume-20261009-remote-recovery-" + suffix + "-definition.json")),
        "history_execution_evidence": "BE-REMOTE-TTL-004-history-run-execution.json",
        "recovery_execution_evidence": "BE-REMOTE-TTL-004-recovery-run-execution.json",
        "history_file_oracle": "BE-REMOTE-TTL-004-history-file-oracle-execution.json",
        "recovery_file_oracle": "BE-REMOTE-TTL-004-recovery-file-oracle-execution.json",
        "local_journal_oracle": "BE-REMOTE-TTL-004-recovery-local-journal-execution.json",
        "proxy_event_oracle": "BE-REMOTE-TTL-004-recovery-request-response-events-execution.json",
        "controls_clear_oracle": "BE-REMOTE-TTL-004-clear-observe-only-delay-execution.json",
    })
    progress("ttl-recovery-business-PASS", status="PASS_FOR_CONFIRMED_REMOTE_TTL_PATH", next_action="Verify SUCCESS recovery Execution in agent-browser; then precise cleanup of TTL-owned tasks/markers and reenrollment case.", ttl_recovery_summary="ttl-recovery-summary.json", recovery_execution_id=recovery_run["id"], history_execution_id=history_run["id"])
    print(json.dumps({"status": "PASS", "case_id": CASE, "history_execution_id": history_run["id"], "recovery_execution_id": recovery_run["id"], "remote_request_id": request_event.get("id"), "delay_ms": delay_event.get("delay_ms"), "marker_verified": True, "journal_verified": True, "controls_cleared": True}))


if __name__ == "__main__":
    main()
