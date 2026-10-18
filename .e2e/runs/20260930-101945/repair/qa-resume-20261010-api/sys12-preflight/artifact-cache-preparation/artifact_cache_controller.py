#!/usr/bin/env python3
"""SYS-12 ArtifactCache fixture controller. Default is local plan only; --execute is grant gated."""
import argparse
import hashlib
import importlib.util
import json
import re
import os
import stat
import time
import uuid
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
OUTER = HERE.parent / "sys12_outer_controller.py"
HELPER = HERE / "artifact_cache_lifecycle.py"
OUTER_SHA256 = "0e213c45180eab1701dfc87aabc9b353d7025f909429f764bc1028b083e353a7"
TARGET_NATIVE2 = "611c02a5-1859-4465-878b-d543b7f6fdec"
REPAIR_IP, REPAIR_PORT = "192.168.100.209", 18080
PROTECTED = ("91f44866-8a28-435e-bbda-2f2367126c00", "611c02a5-1859-4465-878b-d543b7f6fdec",
             "37989297-3bc8-4c0c-b302-7c6efbdba630", "03cb50cc-eab6-4afd-bbe0-f73c4e326773")
MAX_BODY = 2 * 1024 * 1024
READONLY_NODE_WINDOW_SECONDS = 30


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_outer():
    raw = OUTER.read_bytes()
    if sha(raw) != OUTER_SHA256:
        raise RuntimeError("reviewed_outer_controller_sha_mismatch")
    spec = importlib.util.spec_from_file_location("sys12_outer_reviewed", OUTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UploadOpener:
    def __init__(self, base, token):
        self.base, self.token = base, token
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def upload_artifact(self, payload, name, version):
        boundary = "----qa" + uuid.uuid4().hex
        parts = [
            ("name", name.encode()), ("version", version.encode()), ("architecture", b"amd64"),
            ("file", payload),
        ]
        chunks = []
        for key, val in parts:
            chunks.extend([("--" + boundary + "\r\n").encode(),
                           (f'Content-Disposition: form-data; name="{key}"' +
                            ('; filename="payload.bin"' if key == "file" else "") + "\r\n").encode()])
            if key == "file": chunks.append(b"Content-Type: application/octet-stream\r\n")
            chunks.extend([b"\r\n", val, b"\r\n"])
        chunks.append(("--" + boundary + "--\r\n").encode())
        body = b"".join(chunks)
        req = Request(self.base + "/api/artifacts", data=body,
                      headers={"Authorization":"Bearer "+self.token,
                               "Content-Type":"multipart/form-data; boundary="+boundary}, method="POST")
        with self.opener.open(req, timeout=15) as resp:
            raw = resp.read(MAX_BODY + 1)
            code = resp.status
        if len(raw) > MAX_BODY: raise RuntimeError("artifact_upload_response_too_large")
        obj = json.loads(raw)
        if code != 200 or not isinstance(obj, dict): raise RuntimeError("artifact_upload_failed")
        return obj


def exact(api, require, path, ident):
    code, obj, _ = api.request("GET", path)
    require(code, 200, "owned_object_get")
    if not isinstance(obj, dict) or obj.get("id") != ident:
        raise RuntimeError("owned_object_identity_mismatch")
    return obj


def wait_synced(app, api, node_id, node_name, minimum_revision=0):
    deadline = time.monotonic() + 75
    last = None
    while time.monotonic() < deadline:
        last = app.get_node(api, node_id)
        if last.get("hostname") != node_name or last.get("deployment_mode") != "docker" or last.get("host_integration") is not False:
            raise RuntimeError("fixture_node_identity_or_mode_mismatch")
        if last.get("status") == "online" and last.get("sync_status") == "synced" and last.get("global_revision", -1) >= minimum_revision:
            return last
        time.sleep(.5)
    raise RuntimeError("fixture_node_sync_timeout")


def readonly_node_observation_window(app, progress_path, nonce, node_id, sleep=time.sleep):
    for remaining in range(READONLY_NODE_WINDOW_SECONDS, 0, -1):
        app.save(progress_path, {"updated_at_utc": app.utc(),
                                 "status": "OWNED_NODE_UI_READONLY_WINDOW",
                                 "next_action": "read only this owned Node capabilities; no writes",
                                 "nonce": nonce, "node_id": node_id,
                                 "seconds_remaining": remaining})
        sleep(1)
    app.save(progress_path, {"updated_at_utc": app.utc(),
                             "status": "OWNED_NODE_UI_READONLY_WINDOW",
                             "next_action": "observation window ended; continue Artifact creation",
                             "nonce": nonce, "node_id": node_id, "seconds_remaining": 0})


def post_result(app, api, source, source_sha, ledger, result, phase, params, pre_run=None, recovery=False):
    helper_results = result.get("helper_results")
    if not isinstance(helper_results, list):
        raise RuntimeError("helper_results_shape_invalid")
    before_count = len(helper_results)
    got = app.run_helper(api, source, source_sha, ledger, result, phase, params,
                         pre_run=pre_run, recovery=recovery)
    helper_results = result.get("helper_results")
    if (not isinstance(helper_results, list) or len(helper_results) != before_count + 1
            or not isinstance(helper_results[-1], dict)
            or helper_results[-1].get("phase") != phase
            or helper_results[-1].get("remote") != got):
        raise RuntimeError("helper_result_record_missing_or_duplicated")
    if recovery:
        result["status"] = "RECOVERY_PARTIAL"
    app.save(ledger["result_path"], result)
    return got


def read_owned_evidence(path):
    p = Path(path)
    if (not p.is_absolute() or p.parent != HERE or p.parent.resolve() != HERE.resolve()
            or p.resolve().parent != HERE.resolve()):
        raise RuntimeError("recovery_evidence_path_scope_invalid")
    before = p.lstat()
    if (p.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > 4 * 1024 * 1024):
        raise RuntimeError("recovery_evidence_file_guard_failed")
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if ((opened.st_dev, opened.st_ino, opened.st_uid, opened.st_gid, opened.st_mode, opened.st_size)
                != (before.st_dev, before.st_ino, os.geteuid(), before.st_gid, before.st_mode, before.st_size)):
            raise RuntimeError("recovery_evidence_identity_changed_during_open")
        raw = os.read(fd, 4 * 1024 * 1024 + 1)
    finally:
        os.close(fd)
    if len(raw) != before.st_size or len(raw) > 4 * 1024 * 1024:
        raise RuntimeError("recovery_evidence_size_changed_or_limit")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise RuntimeError("recovery_evidence_json_shape_invalid")
    return obj


def exact_delete(api, outer, path, ident, guard, collection, expected_name, expected_name_key="name"):
    # Recovery accepts an already absent ledger-pinned ID only after collection proves
    # neither that ID nor the nonce-owned name has been reused by another object.
    record=outer.delete_exact(api, path, ident, guard, allow_missing=True)
    code, obj, _=api.request("GET",collection)
    outer.require(code,200,"recovery_collection_readback")
    rows=outer.list_rows(obj, (Path(collection).name, "items"))
    if rows is None: raise RuntimeError("recovery_collection_shape_invalid")
    if any(isinstance(x,dict) and (x.get("id")==ident or x.get(expected_name_key)==expected_name) for x in rows):
        raise RuntimeError("recovery_object_id_or_name_still_present")
    code,_,_=api.request("GET",path)
    if code!=404: raise RuntimeError("recovery_object_id_not_404")
    return record


def recover(args, outer, api, helper_source, helper_sha, controller_sha):
    path=Path(args.recover_ledger)
    if not path.is_absolute() or path.parent != HERE or not path.name.startswith("artifact-cache-") or not path.name.endswith("-ledger.json"):
        raise RuntimeError("recovery_ledger_path_scope_invalid")
    st=path.lstat()
    if path.resolve()!=path: raise RuntimeError("recovery_ledger_symlink_or_path_escape")
    if not stat.S_ISREG(st.st_mode) or path.is_symlink() or st.st_uid!=os.geteuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>256*1024:
        raise RuntimeError("recovery_ledger_file_guard_failed")
    ledger=json.loads(path.read_text())
    if (ledger.get("ledger_path")!=str(path) or ledger.get("helper_sha256")!=helper_sha
            or ledger.get("controller_sha256")!=controller_sha or not re.fullmatch(r"[a-f0-9]{12}",ledger.get("nonce",""))):
        raise RuntimeError("recovery_ledger_identity_or_source_mismatch")
    result_path=Path(ledger.get("result_path","")); progress_path=Path(ledger.get("progress_path",""))
    if result_path.parent.resolve()!=HERE.resolve() or progress_path.parent.resolve()!=HERE.resolve():
        raise RuntimeError("recovery_evidence_path_scope_invalid")
    result=read_owned_evidence(result_path)
    if "helper_results" not in result:
        result["helper_results"] = []
    if not isinstance(result.get("helper_results"), list):
        raise RuntimeError("helper_results_shape_invalid")
    result["status"] = "RECOVERY_PARTIAL"
    outer.save(result_path, result)
    ledger["status"] = "RECOVERY_PARTIAL"
    ledger["phase"] = "RECOVERY_PARTIAL"
    ledger["recovery_grant_id"]=args.grant_id
    ledger["recovery_started_at_utc"]=outer.utc()
    outer.save(path,ledger)
    outer.save(progress_path,{"updated_at_utc":outer.utc(),"status":"RECOVERY_PREFLIGHT","next_action":"prove exact helpers terminal before resolving resources"})
    # This exact existing helper routine waits for each recorded Execution terminal before deleting its Task.
    outer.cleanup_helper_tasks(api,ledger)
    outer.prove_helper_records_inactive(api,ledger)
    nonce=ledger["nonce"]; node_name=ledger["node_name"]; app_name=ledger["app_name"]
    sha256=ledger["artifact_sha256"]
    if ledger.get("artifact_create_requested") and not ledger.get("artifact_id"):
        code,obj,_=api.request("GET","/api/artifacts"); outer.require(code,200,"recovery_artifact_list")
        rows=outer.list_rows(obj,("artifacts","items"))
        if rows is None: raise RuntimeError("recovery_artifact_list_shape_invalid")
        matches=[x for x in rows if isinstance(x,dict) and x.get("name")=="qa-artcache-"+nonce and x.get("sha256")==sha256 and x.get("size")==32]
        if len(matches)>1: raise RuntimeError("recovery_artifact_ambiguous")
        if matches: ledger["artifact_id"]=matches[0].get("id")
    if ledger.get("app_create_requested") and not ledger.get("application_id"):
        code,obj,_=api.request("GET","/api/applications"); outer.require(code,200,"recovery_app_list")
        rows=outer.list_rows(obj,("applications","items"))
        if rows is None: raise RuntimeError("recovery_app_list_shape_invalid")
        matches=[x for x in rows if isinstance(x,dict) and x.get("name")==app_name and x.get("artifact_id")==ledger.get("artifact_id") and x.get("unit_name")==ledger["unit_name"]]
        if len(matches)>1: raise RuntimeError("recovery_app_ambiguous")
        if matches: ledger["application_id"]=matches[0].get("id")
    if ledger.get("prepare_requested") and not ledger.get("node_id"):
        code,obj,_=api.request("GET","/api/nodes"); outer.require(code,200,"recovery_node_list")
        rows=outer.list_rows(obj,("nodes","items"))
        if rows is None: raise RuntimeError("recovery_node_list_shape_invalid")
        matches=[x for x in rows if isinstance(x,dict) and x.get("hostname")==node_name]
        if len(matches)>1: raise RuntimeError("recovery_node_ambiguous")
        if matches:
            node=matches[0]
            if node.get("deployment_mode")!="docker" or node.get("host_integration") is not False:
                raise RuntimeError("recovery_node_mode_guard_failed")
            ledger["node_id"],ledger["agent_id"]=node.get("id"),node.get("agent_id")
    outer.save(path,ledger)
    for rec in ledger.get("helper_calls",[]):
        safe=rec.get("safe_result") if isinstance(rec,dict) else None
        if rec.get("phase")=="prepare" and isinstance(safe,dict):
            if ledger.get("root_dev") is None and type(safe.get("root_dev")) is int: ledger["root_dev"]=safe["root_dev"]
            if ledger.get("root_inode") is None and type(safe.get("root_inode")) is int: ledger["root_inode"]=safe["root_inode"]
    outer.save(path,ledger)
    aid,appid,nid=ledger.get("artifact_id"),ledger.get("application_id"),ledger.get("node_id")
    if appid:
        apppath="/api/applications/"+appid
        code,appobj,_=api.request("GET",apppath)
        if code==200:
            if not isinstance(appobj,dict) or appobj.get("id")!=appid or appobj.get("name")!=app_name or appobj.get("artifact_id")!=aid or appobj.get("unit_name")!=ledger["unit_name"]:
                raise RuntimeError("recovery_app_identity_mismatch")
            code,nodes,_=api.request("GET",apppath+"/nodes")
            if code!=200 or not isinstance(nodes,list): raise RuntimeError("recovery_app_nodes_shape_invalid")
            if nid in nodes:
                code,_,_=api.request("POST",apppath+"/assign",{"node_ids":[nid],"remove":True})
                outer.require(code,200,"recovery_app_unassign")
        elif code!=404:
            raise RuntimeError("recovery_app_get_unexpected_status")
        exact_delete(api,outer,apppath,appid,
                     lambda x:x.get("name")==app_name and x.get("artifact_id")==aid and x.get("unit_name")==ledger["unit_name"],
                     "/api/applications",app_name)
        ledger["application_deleted_404"]=True; outer.save(path,ledger)
    if aid:
        exact_delete(api,outer,"/api/artifacts/"+aid,aid,
                     lambda x:x.get("name")=="qa-artcache-"+nonce and x.get("sha256")==sha256 and x.get("size")==32,
                     "/api/artifacts","qa-artcache-"+nonce)
        ledger["artifact_deleted_404"]=True; outer.save(path,ledger)
    if nid:
        exact_delete(api,outer,"/api/nodes/"+nid,nid,
                     lambda x:x.get("hostname")==node_name and x.get("agent_id")==ledger.get("agent_id") and x.get("deployment_mode")=="docker" and x.get("host_integration") is False,
                     "/api/nodes",node_name,"hostname")
        ledger["node_deleted_404"]=True; outer.save(path,ledger)
    for oid, route in ((appid,"applications"),(aid,"artifacts"),(nid,"nodes")):
        if oid:
            code,_,_=api.request("GET",f"/api/{route}/{oid}")
            if code!=404: raise RuntimeError("recovery_api_object_not_404")
    ledger["api_objects_deleted"]=True; outer.save(path,ledger)
    if any(x.get("phase")=="prepare" and x.get("run_requested") for x in ledger.get("helper_calls",[])):
        runtime=post_result(outer,api,helper_source,helper_sha,ledger,result,"cleanup-runtime",{},recovery=True)
        outer.save(path,ledger)
        if (runtime.get("status")=="NO_RUNTIME_ROOT_RESOURCES"
                and runtime.get("root_absent") is True
                and runtime.get("containers_absent") is True
                and runtime.get("network_absent") is True):
            ledger["runtime_root_absence_proven"]=True
        elif (runtime.get("status")=="RUNTIME_REMOVED_ROOT_RETAINED"
                and runtime.get("network_removed") is True):
            if not ledger.get("api_objects_deleted"):
                raise RuntimeError("recovery_root_cleanup_requires_all_api_objects_deleted")
            if type(ledger.get("root_dev")) is not int or type(ledger.get("root_inode")) is not int:
                raise RuntimeError("recovery_external_root_pin_missing")
            root_result=post_result(outer,api,helper_source,helper_sha,ledger,result,"cleanup-root",{"hub_objects_404":True,"artifact_sha256":sha256,"root_dev":ledger["root_dev"],"root_inode":ledger["root_inode"]},recovery=True)
            if (root_result.get("status")!="EXACT_OWNED_ROOT_REMOVED"
                    or root_result.get("root_absent") is not True
                    or root_result.get("hub_objects_404_gate") is not True):
                raise RuntimeError("recovery_root_cleanup_not_complete")
        else:
            raise RuntimeError("recovery_runtime_cleanup_status_unexpected")
    ledger["status"]="RECOVERY_COMPLETE"; ledger["phase"]="RECOVERY_COMPLETE"; outer.save(path,ledger)
    result["status"]="RECOVERY_COMPLETE"; outer.save(result_path,result)
    outer.save(progress_path,{"updated_at_utc":outer.utc(),"status":"RECOVERY_COMPLETE","next_action":"Root review; do not rerun cache business phases","nonce":nonce})


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--grant-id",default="")
    parser.add_argument("--approved-helper-sha256",default="")
    parser.add_argument("--approved-controller-sha256",default="")
    parser.add_argument("--connection",default="")
    parser.add_argument("--protected-node-ids",default="")
    parser.add_argument("--recover-ledger",default="")
    parser.add_argument("--readonly-node-observation-window",action="store_true",
                         help="wait one fixed 30-second read-only window after owned Node sync; requires explicit grant review")
    args=parser.parse_args()
    if not args.execute:
        print(json.dumps({"status":"DRAFT_NO_ACTION","scope":"SYS12_ARTIFACT_CACHE_ONLY",
                          "helper_path":str(HELPER),"execute_requires_root_grant":True},sort_keys=True))
        return
    app=load_outer()
    helper_raw=HELPER.read_bytes(); helper_source=helper_raw.decode("utf-8")
    compile(helper_source,str(HELPER),"exec")
    helper_sha=sha(helper_raw); controller_sha=sha(Path(__file__).read_bytes())
    if (not args.grant_id or args.approved_helper_sha256!=helper_sha
            or args.approved_controller_sha256!=controller_sha):
        raise RuntimeError("grant_or_reviewed_hash_mismatch")
    protected=tuple(x for x in args.protected_node_ids.split(",") if x) if args.protected_node_ids else PROTECTED
    if protected!=PROTECTED or len(set(protected))!=4 or TARGET_NATIVE2 not in protected:
        raise RuntimeError("protected_node_pin_invalid")
    base,token=app.read_private(args.connection)
    api=app.API(base,token)
    if args.recover_ledger:
        if args.readonly_node_observation_window:
            raise RuntimeError("readonly_window_not_valid_for_recovery")
        recover(args,app,api,helper_source,helper_sha,controller_sha)
        return
    nonce=uuid.uuid4().hex[:12]
    owner="qa-resume-20261012-artcache-"+nonce
    node_name="qa-artcache-"+nonce
    app_name="qa-artcache-app-"+nonce
    unit_name="nodesteer-qa-artcache-"+nonce+".service"
    payload=hashlib.sha256(("nodesteer-artifact-cache-v1:"+nonce).encode()).digest()
    artifact_sha=sha(payload)
    result_path=HERE/("artifact-cache-"+nonce+"-result.json")
    ledger_path=HERE/("artifact-cache-"+nonce+"-ledger.json")
    progress_path=HERE/("artifact-cache-"+nonce+"-progress.json")
    if any(p.exists() for p in (result_path,ledger_path,progress_path)):
        raise RuntimeError("local_evidence_path_collision")
    ledger={"nonce":nonce,"owner":owner,"node_name":node_name,"app_name":app_name,
            "unit_name":unit_name,"artifact_sha256":artifact_sha,"artifact_size":len(payload),
            "helper_calls":[],"helper_call_count_total":0,"helper_call_count_normal":0,
            "helper_call_count_recovery":0,"node_id":None,"agent_id":None,"artifact_id":None,
            "application_id":None,"grant_id":args.grant_id,"helper_sha256":helper_sha,
            "controller_sha256":controller_sha,"ledger_path":str(ledger_path),
            "result_path":str(result_path),"progress_path":str(progress_path),
            "api_objects_deleted":False}
    result={"status":"STARTED","scope":"SYS12_ARTIFACT_CACHE_ONLY","nonce":nonce,
            "authority":"repair:18080","helper_sha256":helper_sha,"controller_sha256":controller_sha,
            "helper_results":[],
            "artifact_size":32,"artifact_sha256":artifact_sha,"coverage":{},"api_evidence":[]}
    api.on_response=lambda x:(result["api_evidence"].append(x),app.save(result_path,result))
    api.progress_path,api.nonce=progress_path,nonce
    app.save(ledger_path,ledger); app.save(result_path,result)
    app.save(progress_path,{"updated_at_utc":app.utc(),"status":"PREFLIGHT","next_action":"verify protected Nodes and Native2"})
    try:
        for nid in protected:
            node=app.get_node(api,nid)
            if node.get("status")!="online" or node.get("sync_status")!="synced": raise RuntimeError("protected_node_unhealthy")
        n2=app.get_node(api,TARGET_NATIVE2)
        if n2.get("status")!="online" or n2.get("sync_status")!="synced": raise RuntimeError("native2_unhealthy")
        code,nodes_obj,_=api.request("GET","/api/nodes"); app.require(code,200,"nodes_list")
        nodes=app.list_rows(nodes_obj,("nodes","items"))
        if nodes is None: raise RuntimeError("nodes_list_shape_invalid")
        before_ids={x.get("id") for x in nodes if isinstance(x,dict)}
        if any(isinstance(x,dict) and x.get("hostname")==node_name for x in nodes): raise RuntimeError("fixture_node_name_collision")
        def adjacent_name_guard(): app.ensure_node_name_unused(api,node_name,before_ids)
        ledger["prepare_requested"]=True; app.save(ledger_path,ledger)
        p=post_result(app,api,helper_source,helper_sha,ledger,result,"prepare",{"artifact_sha256":artifact_sha},pre_run=adjacent_name_guard)
        if (type(p.get("root_dev")) is int and type(p.get("root_inode")) is int
                and p.get("owner_root_marker_valid", True) is True):
            ledger["root_dev"],ledger["root_inode"]=p["root_dev"],p["root_inode"]; app.save(ledger_path,ledger)
        if p.get("status")!="CONTAINER_A_STARTED": raise RuntimeError("prepare_result_mismatch")
        if ledger.get("root_dev") is None or ledger.get("root_inode") is None:
            raise RuntimeError("prepare_root_identity_missing")
        node=app.wait_node_by_name(api,node_name)
        if node["id"] in before_ids: raise RuntimeError("fresh_node_id_collision")
        ledger["node_id"],ledger["agent_id"]=node["id"],node["agent_id"]; app.save(ledger_path,ledger)
        if args.readonly_node_observation_window:
            readonly_node_observation_window(app,progress_path,nonce,node["id"])
        code,arts_obj,_=api.request("GET","/api/artifacts"); app.require(code,200,"artifact_list")
        arts=app.list_rows(arts_obj,("artifacts","items"))
        if arts is None: raise RuntimeError("artifact_list_shape_invalid")
        if any(isinstance(x,dict) and x.get("sha256")==artifact_sha for x in arts): raise RuntimeError("artifact_sha_collision")
        ledger["artifact_create_requested"]=True; app.save(ledger_path,ledger)
        upload=UploadOpener(base,token).upload_artifact(payload,"qa-artcache-"+nonce,"1.0.0")
        aid=upload.get("id")
        if (not isinstance(aid,str) or upload.get("sha256")!=artifact_sha or upload.get("size")!=32): raise RuntimeError("artifact_upload_metadata_mismatch")
        ledger["artifact_id"]=aid; app.save(ledger_path,ledger)
        read=exact(api,app.require,"/api/artifacts/"+aid,aid)
        if read.get("sha256")!=artifact_sha or read.get("size")!=32: raise RuntimeError("artifact_get_metadata_mismatch")
        code,apps_obj,_=api.request("GET","/api/applications"); app.require(code,200,"apps_list")
        apps=app.list_rows(apps_obj,("applications","items"))
        if apps is None or any(isinstance(x,dict) and x.get("name")==app_name for x in apps): raise RuntimeError("app_name_preflight_failed")
        body={"name":app_name,"description":"owned un-deployed ArtifactCache fixture","artifact_id":aid,
              "version":"1.0.0","binary_path":"/opt/nodesteer/qa-artcache-not-deployed","arguments":[],
              "environment":{},"config":"","unit_name":unit_name}
        ledger["app_create_requested"]=True; app.save(ledger_path,ledger)
        code,appobj,_=api.request("POST","/api/applications",body)
        if code!=200 or not isinstance(appobj,dict) or not isinstance(appobj.get("id"),str): raise RuntimeError("app_create_failed")
        appid=appobj["id"]; ledger["application_id"]=appid; app.save(ledger_path,ledger)
        ar=exact(api,app.require,"/api/applications/"+appid,appid)
        if ar.get("artifact_id")!=aid or ar.get("unit_name")!=unit_name: raise RuntimeError("app_get_metadata_mismatch")
        code,_,_=api.request("POST","/api/applications/"+appid+"/assign",{"node_ids":[node["id"]],"remove":False})
        app.require(code,200,"app_assign")
        code,assignment,_=api.request("GET","/api/applications/"+appid+"/state")
        if code!=200 or not isinstance(assignment,list): raise RuntimeError("app_state_read_failed")
        code,assigned_nodes,_=api.request("GET","/api/applications/"+appid+"/nodes")
        if code!=200 or assigned_nodes!=[node["id"]]: raise RuntimeError("app_assignment_readback_mismatch")
        node=wait_synced(app,api,node["id"],node_name,node.get("global_revision",0))
        # Agent Sync includes assigned Apps and prefetches ArtifactCache without deploying.
        a=post_result(app,api,helper_source,helper_sha,ledger,result,"inspect-a",{
            "app_id":appid,"artifact_id":aid,"artifact_sha256":artifact_sha,"unit_name":unit_name,
            "node_id":node["id"],"agent_id":node["agent_id"],"hub_revision":node["global_revision"]})
        if a.get("status")!="ARTIFACT_CACHE_A_VERIFIED": raise RuntimeError("cache_A_not_verified")
        app.save(ledger_path,ledger)
        db_a=a.get("local_database",{})
        if (db_a.get("identity")!={"node_id":node["id"],"agent_id":node["agent_id"]}
                or db_a.get("application",{}).get("id")!=appid
                or db_a.get("artifact_cache",{}).get("sha256")!=artifact_sha):
            raise RuntimeError("cache_A_business_identity_mismatch")
        code,execs,_=api.request("GET","/api/applications/"+appid+"/executions")
        if code!=200 or not isinstance(execs,list) or execs: raise RuntimeError("unexpected_app_deploy_execution")
        b=post_result(app,api,helper_source,helper_sha,ledger,result,"recreate-b",{
            "app_id":appid,"artifact_id":aid,"artifact_sha256":artifact_sha,"unit_name":unit_name,
            "node_id":node["id"],"agent_id":node["agent_id"]})
        if b.get("status")!="CONTAINER_B_STARTED": raise RuntimeError("B_recreate_mismatch")
        pre_b=b.get("pre_b_persistence",{})
        if ((db_a.get("db_dev"),db_a.get("db_inode"))!=(pre_b.get("db_dev"),pre_b.get("db_inode"))
                or db_a.get("artifact_cache")!=pre_b.get("artifact_cache")
                or db_a.get("application")!=pre_b.get("application") or db_a.get("identity")!=pre_b.get("identity")
                or pre_b.get("global_revision",-1)<db_a.get("global_revision",-1)):
            raise RuntimeError("A_to_preB_persistence_mismatch")
        node=wait_synced(app,api,node["id"],node_name,node.get("global_revision",0))
        if node.get("id")!=ledger["node_id"] or node.get("agent_id")!=ledger["agent_id"]:
            raise RuntimeError("node_or_agent_identity_changed_after_recreate")
        final=post_result(app,api,helper_source,helper_sha,ledger,result,"inspect-b",{
            "app_id":appid,"artifact_id":aid,"artifact_sha256":artifact_sha,"unit_name":unit_name,
            "node_id":node["id"],"agent_id":node["agent_id"],"hub_revision":node["global_revision"]})
        if final.get("status")!="ARTIFACT_CACHE_PERSISTED_A_TO_B": raise RuntimeError("B_cache_persistence_mismatch")
        db_b=final.get("local_database",{})
        if ((db_b.get("db_dev"),db_b.get("db_inode"))!=(db_a.get("db_dev"),db_a.get("db_inode"))
                or db_b.get("artifact_cache")!=db_a.get("artifact_cache") or db_b.get("application")!=db_a.get("application")
                or db_b.get("identity")!=db_a.get("identity") or db_b.get("global_revision",-1)<db_a.get("global_revision",-1)):
            raise RuntimeError("A_to_B_persistence_mismatch")
        code,execs,_=api.request("GET","/api/applications/"+appid+"/executions")
        if code!=200 or not isinstance(execs,list) or execs: raise RuntimeError("app_execution_list_not_empty")
        # Exact reverse-order cleanup; never call deploy.
        code,_,_=api.request("POST","/api/applications/"+appid+"/assign",{"node_ids":[node["id"]],"remove":True}); app.require(code,200,"app_unassign")
        code,remaining,_=api.request("GET","/api/applications/"+appid+"/nodes")
        if code!=200 or remaining!=[]: raise RuntimeError("app_unassign_readback_mismatch")
        app.delete_exact(api,"/api/applications/"+appid,appid,lambda x:x.get("artifact_id")==aid and x.get("unit_name")==unit_name)
        app.delete_exact(api,"/api/artifacts/"+aid,aid,lambda x:x.get("sha256")==artifact_sha and x.get("size")==32)
        app.delete_exact(api,"/api/nodes/"+node["id"],node["id"],lambda x:x.get("hostname")==node_name and x.get("agent_id")==ledger["agent_id"] and x.get("deployment_mode")=="docker" and x.get("host_integration") is False)
        ledger["api_objects_deleted"]=True; app.save(ledger_path,ledger)
        for nid in protected:
            pn=app.get_node(api,nid)
            if pn.get("status")!="online" or pn.get("sync_status")!="synced": raise RuntimeError("protected_node_unhealthy_after_cleanup")
        for oid,route in ((appid,"applications"),(aid,"artifacts"),(node["id"],"nodes")):
            code,_,_=api.request("GET",f"/api/{route}/{oid}")
            if code!=404: raise RuntimeError("api_cleanup_not_404")
        ledger["api_objects_deleted"]=True; app.save(ledger_path,ledger)
        rt=post_result(app,api,helper_source,helper_sha,ledger,result,"cleanup-runtime",{})
        if rt.get("status")!="RUNTIME_REMOVED_ROOT_RETAINED": raise RuntimeError("runtime_cleanup_mismatch")
        root=post_result(app,api,helper_source,helper_sha,ledger,result,"cleanup-root",{"hub_objects_404":True,"artifact_sha256":artifact_sha,"root_dev":ledger.get("root_dev"),"root_inode":ledger.get("root_inode")})
        if root.get("status")!="EXACT_OWNED_ROOT_REMOVED": raise RuntimeError("root_cleanup_mismatch")
        result["status"]="SYS12_ARTIFACT_CACHE_A_TO_B_PASS"
        result["coverage"]={"artifact_db_row_and_physical_sha": "PASS", "A_to_B_same_data_dir": "PASS",
                            "assigned_app_prefetch_without_deploy":"PASS", "app_execution_count":0,
                            "managed_app_unit_created":False,"script_cache":"NOT_TESTED","artifact_deploy_fetch":"NOT_TESTED"}
        app.save(result_path,result); ledger["status"]=result["status"]; ledger["phase"]="COMPLETE"; app.save(ledger_path,ledger)
    except Exception as exc:
        result["status"]="FAILED_OR_PARTIAL"; result["safe_error"]="exception_"+type(exc).__name__
        app.save(result_path,result); ledger["status"]=result["status"]; ledger["last_safe_error"]=result["safe_error"]; app.save(ledger_path,ledger)
        raise

if __name__=="__main__":
    try: main()
    except Exception as exc:
        print(json.dumps({"status":"SAFE_STOP","safe_error":"exception_"+type(exc).__name__},sort_keys=True))
        raise SystemExit(2)
