#!/usr/bin/env python3
"""Delete only inventoried TTL-owned marker files; preserve all case directories."""
import json
from pathlib import Path
import test_api as api
import run_ttl_recovery_retry as case

HERE=Path(__file__).resolve().parent


def main():
    inv=json.loads((HERE/"ttl-marker-inventory.json").read_text())
    if inv.get("status")!="PASS_FOR_EXACT_CLEANUP" or inv.get("unknown_or_mismatched"):
        raise RuntimeError("marker_inventory_not_approved_for_exact_cleanup")
    records=inv.get("files",[])
    if not records or len(records)!=8:
        raise RuntimeError("marker_inventory_count_guard_failed")
    expected=[]
    for x in records:
        expected.append({"path":x["path"],"content":x["expected_content"],"sha256":x["sha256"],"mode":x["mode"],"uid":x["uid"]})
    body=case.guard(case.claims())+r'''
root=D/"data"/"qa-be-remote-ttl"; entries=json.loads(%r); result=[]
if root.is_symlink() or not root.resolve().is_relative_to(D.resolve()): raise RuntimeError("ttl_root_escape")
for x in entries:
 p=Path(x["path"])
 if p.is_symlink() or not p.resolve().is_relative_to(root.resolve()) or p.parent.parent.resolve()!=root.resolve(): raise RuntimeError("marker_path_escape")
 if not p.exists():
  result.append({"path":str(p),"status":"already_absent"}); continue
 st=p.stat(); b=p.read_bytes(); mode=oct(stat.S_IMODE(st.st_mode))
 if st.st_uid!=65534 or st.st_uid!=x["uid"] or mode!=x["mode"] or hashlib.sha256(b).hexdigest()!=x["sha256"] or b.decode()!=x["content"]: raise RuntimeError("marker_identity_content_or_mode_mismatch")
 p.unlink()
 if p.exists() or p.is_symlink(): raise RuntimeError("marker_unlink_verification_failed")
 result.append({"path":str(p),"status":"deleted","sha256":x["sha256"],"mode":mode,"uid":st.st_uid})
print(json.dumps({"status":"PASS","deleted_count":sum(x["status"]=="deleted" for x in result),"already_absent_count":sum(x["status"]=="already_absent" for x in result),"directories_removed":False,"files":result},separators=(",",":")))
''' % json.dumps(expected,separators=(",",":"))
    native4=json.loads((HERE.parent/"qa-resume-20261008-backend/setup/setup-summary.json").read_text())["target_native4"]["node_id"]
    result=api.native4_command("BE-REMOTE-TTL-exact-marker-cleanup",body,native4,60)
    if result.get("status")!="SUCCESS" or result.get("output",{}).get("status")!="PASS": raise RuntimeError("exact_marker_cleanup_failed")
    evidence={"status":"PASS","inventory":"ttl-marker-inventory.json","files":result["output"]["files"],"deleted_count":result["output"]["deleted_count"],"already_absent_count":result["output"]["already_absent_count"],"directories_removed":False,"native4_task_id":result["task_id"],"native4_execution_id":result["execution_id"]}
    api.write_json(HERE/"ttl-marker-cleanup.json",evidence)
    print(json.dumps({"status":"PASS","deleted":evidence["deleted_count"],"already_absent":evidence["already_absent_count"],"directories_removed":False}))


if __name__=="__main__": main()

