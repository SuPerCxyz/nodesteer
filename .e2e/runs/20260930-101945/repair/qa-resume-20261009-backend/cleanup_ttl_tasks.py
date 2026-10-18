#!/usr/bin/env python3
"""Inventory and precisely delete only TTL-case-owned Task resources."""
import hashlib, json, sys, time
from pathlib import Path
import test_api as api

HERE=Path(__file__).resolve().parent
OLD=HERE.parent/"qa-resume-20261008-backend/cases"
CHILD="15961ef7-81b4-43f3-80c8-1b6977ca2aea"
NATIVE4="03cb50cc-eab6-4afd-bbe0-f73c4e326773"
PREFIXES=("BE-REMOTE-TTL","qa-resume-20261008-remote-","qa-resume-20261009-remote-","qa-resume-20261009-BE-REMOTE-TTL")


def candidates():
    found={}
    for root in (OLD,HERE):
        for p in root.glob("*-created.json"):
            try: d=json.loads(p.read_text())
            except Exception: continue
            rid=d.get("id") or d.get("task_id")
            name=d.get("name") or d.get("task_name")
            if rid and name and name.startswith(PREFIXES):
                row=found.setdefault(rid,{"task_id":rid,"recorded_name":name,"source_files":[],"expected_target":d.get("target_node_id")})
                if row["recorded_name"]!=name: raise RuntimeError("duplicate_task_id_name_mismatch")
                row["source_files"].append(str(p))
        # Add already-confirmed TTL checkpoint/summary IDs even if a created record is missing.
    cp=json.loads((OLD/"BE-REMOTE-TTL-004-checkpoint.json").read_text())
    ids=[cp.get("history_task_id"),cp.get("condition_task_id")]+[x.get("task_id") for x in cp.get("completed_negative_cases",[])]
    summary=json.loads((HERE/"ttl-recovery-summary.json").read_text())
    ids += summary.get("cleanup_task_ids",[])
    for rid in filter(None,ids):
        found.setdefault(rid,{"task_id":rid,"recorded_name":None,"source_files":[str(OLD/"BE-REMOTE-TTL-004-checkpoint.json")],"expected_target":None})
    return found


def inspect():
    result=[]
    for rid,row in candidates().items():
        path="/api/tasks/"+rid
        status,raw=api.request("GET",path)
        if status==404:
            result.append({**row,"state":"already-absent","get_http":404})
            continue
        if status!=200:
            raise RuntimeError("task_inventory_http_"+str(status))
        task=json.loads(raw)
        name=task.get("name","")
        target=task.get("target",{}).get("node_ids")
        if row.get("recorded_name") and name!=row["recorded_name"]:
            raise RuntimeError("task_name_record_mismatch:"+rid)
        if not name.startswith(PREFIXES) or target not in ([CHILD],[NATIVE4]) or task.get("retry")!=0 or task.get("schedule"):
            raise RuntimeError("task_owner_target_or_policy_guard_failed:"+rid)
        command=task.get("command","")
        command_sha=hashlib.sha256(command.encode()).hexdigest()
        # Compare a saved definition when present.
        def_path=None
        for src in row["source_files"]:
            p=Path(src)
            candidate=p.with_name(p.name.replace("-created.json","-definition.json"))
            if candidate.exists():
                try: definition=json.loads(candidate.read_text())
                except Exception: continue
                payload=definition.get("payload") or definition.get("task") or {}
                expected_command=payload.get("command")
                if expected_command is not None:
                    if expected_command!=command: raise RuntimeError("task_command_definition_mismatch:"+rid)
                    def_path=str(candidate);break
        result.append({**row,"state":"present","get_http":200,"name":name,"target_node_ids":target,"retry":task.get("retry"),"schedule_present":bool(task.get("schedule")),"condition":task.get("condition"),"command_sha256":command_sha,"definition_file":def_path})
    return result


def main():
    records=inspect()
    plan={"stage":"ttl_task_cleanup_plan","inspected_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"candidate_count":len(records),"present_count":sum(x["state"]=="present" for x in records),"absent_count":sum(x["state"]=="already-absent" for x in records),"records":records}
    api.write_json(HERE/"ttl-task-cleanup-plan.json",plan)
    if "--execute" not in sys.argv:
        print(json.dumps({k:plan[k] for k in ("candidate_count","present_count","absent_count")}))
        return
    done=[]
    for record in records:
        if record["state"]=="already-absent":
            done.append({**record,"cleanup_status":"already-absent"})
            continue
        name=record["name"]
        prefix=next(x for x in PREFIXES if name.startswith(x))
        cleanup=api.delete_task(record["task_id"],"ttl-task-"+record["task_id"]+"-cleanup",record["target_node_ids"][0],prefix)
        done.append({**record,"cleanup_status":"deleted","delete_http":cleanup["delete_http"],"get_after_delete_http":cleanup["get_after_delete_http"]})
        api.write_json(HERE/"ttl-task-cleanup-ledger.json",{"status":"RUNNING","records":done,"remaining":len(records)-len(done)})
    final={"status":"PASS" if all(x.get("cleanup_status") in {"deleted","already-absent"} and x.get("get_after_delete_http",404)==404 for x in done) else "PARTIAL","records":done,"preserved_execution_history":True,"scope":{"child_node_id":CHILD,"native4_node_id":NATIVE4,"allowed_name_prefixes":list(PREFIXES)}}
    api.write_json(HERE/"ttl-task-cleanup-ledger.json",final)
    print(json.dumps({"status":final["status"],"deleted":sum(x["cleanup_status"]=="deleted" for x in done),"already_absent":sum(x["cleanup_status"]=="already-absent" for x in done)}))


if __name__=="__main__": main()

