#!/usr/bin/env python3
"""Capture exact owned-child reenrollment baseline without emitting credentials."""
import hashlib,json,urllib.parse
from pathlib import Path
import test_api as api
import run_ttl_recovery_retry as case

HERE=Path(__file__).resolve().parent
CHILD="15961ef7-81b4-43f3-80c8-1b6977ca2aea"
LABELS={"qa_reenroll_20261009":"owned_child_label_preserve"}


def main():
    c=case.claims()
    node=api.get("/api/nodes/"+CHILD)
    if node.get("id")!=CHILD or node.get("agent_id")!=c["agent_id"] or node.get("status")!="online" or node.get("sync_status")!="synced" or node.get("labels")!=LABELS:
        raise RuntimeError("owned_child_baseline_mismatch")
    setup=json.loads((HERE.parent/"qa-resume-20261008-backend/setup/setup-summary.json").read_text())
    native4=setup["target_native4"]["node_id"]
    source=case.guard(c)+r'''
import hashlib,json,sqlite3,re
from pathlib import Path
cfg=Path(json.loads((D/"owner-manifest.json").read_text())["config"]["path"])
if cfg.is_symlink() or not cfg.resolve().is_relative_to(D.resolve()): raise RuntimeError("child_config_path_escape")
st=cfg.stat()
if st.st_uid!=65534 or stat.S_IMODE(st.st_mode)!=0o600: raise RuntimeError("child_config_owner_or_mode_mismatch")
lines=cfg.read_text().splitlines()
def scalar(key):
 rows=[x for x in lines if re.match(r"^\s*"+re.escape(key)+r"\s*:",x)]
 if len(rows)!=1: raise RuntimeError("child_config_scalar_missing_or_duplicate")
 value=json.loads(rows[0].split(":",1)[1].strip())
 if not isinstance(value,str): raise RuntimeError("child_config_scalar_not_string")
 return value
config={k:scalar(k) for k in ("agent_id","node_name","node_ip","hub_url","registration_token")}
if config.get("agent_id")!=C["agent_id"] or config.get("node_name")!=%r or config.get("node_ip")!=%r or config.get("hub_url")!=("ws://"+C["proxy"]["ip"]+":"+str(C["proxy"]["port"])+"/"): raise RuntimeError("child_config_identity_mismatch")
grant=config.get("registration_token")
if not isinstance(grant,str) or not grant or hashlib.sha256(grant.encode()).hexdigest()[:16]!=json.loads((D/"owner-manifest.json").read_text())["registration_token_fingerprint"]: raise RuntimeError("child_config_grant_fingerprint_mismatch")
db=D/"data"/"state.db"; conn=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=3)
try:
 rows=dict(conn.execute("SELECT key,value FROM identity WHERE key IN ('node_id','agent_id','credential')").fetchall())
finally: conn.close()
if rows.get("node_id")!=C["node_id"] or rows.get("agent_id")!=C["agent_id"] or not rows.get("credential"): raise RuntimeError("local_identity_mismatch")
print(json.dumps({"status":"PASS","node_id":rows["node_id"],"agent_id":rows["agent_id"],"credential_sha256":hashlib.sha256(rows["credential"].encode()).hexdigest(),"config_sha256":hashlib.sha256(cfg.read_bytes()).hexdigest(),"config_mode":oct(stat.S_IMODE(st.st_mode)),"config_uid":st.st_uid,"registration_token_fingerprint":hashlib.sha256(grant.encode()).hexdigest()[:16],"secrets_emitted":False},separators=(",",":")))
''' % (node["hostname"],node["ip"])
    result=api.native4_command("qa-resume-20261009-reenroll-baseline-identity",source,native4,45)
    if result.get("status")!="SUCCESS" or result.get("output",{}).get("status")!="PASS": raise RuntimeError("owned_child_identity_baseline_failed")
    path="/api/executions?node_id="+urllib.parse.quote(CHILD,safe="")+"&limit=500"
    status,raw=api.request("GET",path)
    if status!=200: raise RuntimeError("node_execution_history_baseline_http")
    executions=json.loads(raw); ids=sorted(x["id"] for x in executions if x.get("id"))
    record={"status":"PASS","node":{k:node.get(k) for k in ("id","agent_id","hostname","ip","status","sync_status","labels","first_seen","last_seen","deployment_mode","agent_version")},"child_identity":result["output"],"execution_http":status,"execution_count":len(ids),"execution_ids":ids,"execution_ids_sha256":hashlib.sha256("\n".join(ids).encode()).hexdigest(),"history_status_counts":{s:sum(1 for x in executions if x.get("status")==s) for s in sorted({x.get("status","unknown") for x in executions})},"owner_ledger_manifest_sha256":c["owner_manifest_sha256"],"native4_identity_oracle_task_id":result["task_id"],"native4_identity_oracle_execution_id":result["execution_id"]}
    api.write_json(HERE/"reenroll-case-baseline.json",record)
    print(json.dumps({"status":"PASS","node_id":CHILD,"agent_id":record["node"]["agent_id"],"labels":record["node"]["labels"],"credential_fingerprint_stored":True,"execution_count":len(ids),"execution_ids_sha256":record["execution_ids_sha256"]}))


if __name__=="__main__":main()

