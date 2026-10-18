#!/usr/bin/env python3
"""Read-only inventory of exact TTL test marker files under the owned case directory."""
import json
import re
from pathlib import Path
import test_api as api
import run_ttl_recovery_retry as case

HERE=Path(__file__).resolve().parent


def main():
    c=case.claims()
    root=Path(c["owner_dir"])/"data"/"qa-be-remote-ttl"
    source=case.guard(c)+r'''
import os,re
root=D/"data"/"qa-be-remote-ttl"
if root.is_symlink() or not root.resolve().is_relative_to(D.resolve()): raise RuntimeError("owned_test_root_escape")
rows=[]
if root.exists():
 for folder in sorted(root.iterdir()):
  if folder.is_symlink() or not folder.is_dir() or not re.fullmatch(r"[0-9a-f]{8}",folder.name): raise RuntimeError("unexpected_case_directory")
  st=folder.stat()
  if st.st_uid!=C["directory"]["uid"] or not folder.resolve().is_relative_to(root.resolve()): raise RuntimeError("case_directory_owner_escape")
  for p in sorted(folder.iterdir()):
   if p.is_symlink() or not p.is_file() or p.suffix!=".txt": continue
   st=p.stat(); b=p.read_bytes()
   rows.append({"path":str(p),"realpath":str(p.resolve()),"name":p.name,"bytes":len(b),"sha256":hashlib.sha256(b).hexdigest(),"mode":oct(stat.S_IMODE(st.st_mode)),"uid":st.st_uid,"content":b.decode(errors="replace")})
print(json.dumps({"status":"PASS","root":str(root),"files":rows},separators=(",",":")))
'''
    native4=json.loads((HERE.parent/"qa-resume-20261008-backend/setup/setup-summary.json").read_text())["target_native4"]["node_id"]
    result=api.native4_command("qa-resume-20261009-ttl-marker-inventory",source,native4,45)
    if result.get("status")!="SUCCESS" or result.get("output",{}).get("status")!="PASS": raise RuntimeError("marker_inventory_failed")
    files=result["output"].get("files",[])
    expected={"history.txt":"history-success","valid-remote-control.txt":"remote-control-success","recovery-success.txt":"remote-recovery-success"}
    unknown=[]
    for x in files:
        if x["name"] not in expected or x["content"]!=expected[x["name"]] or x["uid"]!=65534 or x["mode"] not in {"0o600","0o644"}: unknown.append(x)
    evidence={"status":"PASS_FOR_EXACT_CLEANUP" if not unknown else "REVIEW_REQUIRED","root":result["output"]["root"],"files":[{k:v for k,v in x.items() if k!="content"}|{"expected_content":expected.get(x["name"])} for x in files],"legacy_mode_0644_paths":[x["path"] for x in files if x["mode"]=="0o644"],"unknown_or_mismatched":unknown,"native4_task_id":result["task_id"],"native4_execution_id":result["execution_id"]}
    api.write_json(HERE/"ttl-marker-inventory.json",evidence)
    print(json.dumps({"status":evidence["status"],"marker_file_count":len(files),"unknown_or_mismatched_count":len(unknown),"evidence":"ttl-marker-inventory.json"}))


if __name__=="__main__": main()

