#!/usr/bin/env python3
"""One granted Native1 readonly ArtifactURL helper Run, with exact Task cleanup."""
import hashlib, json, os, re, shlex, stat, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

HERE = Path(__file__).resolve().parent
HELPER = HERE / "native1_app_artifact_url_readonly.py"
HELPER_SHA = "1d10174b39a4f85fb60d0343db85c550db857a305defa59851a721b4211ddcdc"
CONNECTION = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json")
NODE = "91f44866-8a28-435e-bbda-2f2367126c00"
AGENT = "agent-d21d5e7d-0ff0-4355-b8de-4889dc865343"
APP = "804e6b14-eb84-4dda-90de-8bcab9586c4c"
HOST, PORT = "192.168.100.209", 18080
TERMINAL = {"SUCCESS", "FAILED", "BLOCKED", "TIMED_OUT", "CANCELED"}
MAX_BODY = 2 * 1024 * 1024

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None

class SafeStop(Exception): pass

def utc(): return datetime.now(timezone.utc).isoformat()
def digest(raw): return hashlib.sha256(raw).hexdigest()
def save(path, obj):
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    raw = (json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f: f.write(raw); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

def read_private():
    st = CONNECTION.lstat(); parent = CONNECTION.parent.lstat(); uid = os.geteuid()
    if (not stat.S_ISREG(st.st_mode) or CONNECTION.is_symlink() or st.st_uid != uid or stat.S_IMODE(st.st_mode) != 0o600
            or not stat.S_ISDIR(parent.st_mode) or CONNECTION.parent.is_symlink() or CONNECTION.parent.resolve() != CONNECTION.parent
            or CONNECTION.resolve() != CONNECTION or parent.st_uid != uid or stat.S_IMODE(parent.st_mode) != 0o700):
        raise SafeStop("private_connection_owner_mode_guard_failed")
    fd = os.open(CONNECTION, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fs = os.fstat(fd)
        if (fs.st_dev, fs.st_ino, fs.st_uid, stat.S_IMODE(fs.st_mode)) != (st.st_dev, st.st_ino, uid, 0o600): raise SafeStop("private_connection_changed")
        raw = os.read(fd, 65537)
    finally: os.close(fd)
    if len(raw) > 65536: raise SafeStop("private_connection_size_limit")
    obj = json.loads(raw); base = obj.get("hub_url", "").rstrip("/"); token = obj.get("token")
    u = urlsplit(base)
    if (u.scheme != "http" or u.hostname != HOST or u.port != PORT or u.username or u.password or u.query or u.fragment or u.path not in ("", "/")
            or not isinstance(token, str) or not token): raise SafeStop("repair_authority_guard_failed")
    return base, token

class API:
    def __init__(self, base, token): self.base, self.token, self.opener = base, token, build_opener(ProxyHandler({}), NoRedirect())
    def request(self, method, path, body=None, timeout=12, limit=MAX_BODY):
        headers = {"Authorization": "Bearer " + self.token}; data = None
        if body is not None: data=json.dumps(body,separators=(",", ":")).encode(); headers["Content-Type"]="application/json"
        req=Request(self.base+path,data=data,headers=headers,method=method)
        try:
            with self.opener.open(req,timeout=timeout) as r: code,raw=r.status,r.read(limit+1)
        except HTTPError as e: code,raw=e.code,e.read(limit+1)
        except (URLError,TimeoutError,OSError) as e: return None,None,{"transport_error_type":type(e).__name__}
        if len(raw)>limit: return code,None,{"body_too_large":True,"bytes":len(raw)}
        try: obj=json.loads(raw) if raw else None
        except (ValueError,TypeError): obj=None
        return code,obj,{"bytes":len(raw),"body_sha256":digest(raw)}

def rows(obj, keys=()):
    if isinstance(obj,list): return obj
    if isinstance(obj,dict):
        for key in keys:
            if isinstance(obj.get(key),list): return obj[key]
    return None

def safe_response(path, method, code, meta):
    return {"method":method,"path":path,"http":code,**meta}

def phase(outdir,status,next_action):
    save(outdir/"progress.json",{"updated_at_utc":utc(),"status":status,"next_action":next_action})

def safe_helper(out):
    allowed={"status","safe_error","safe_error_type","node_id","agent_id","app_id","application_row_count","agent_pid","agent_start_ticks","agent_uid","agent_proc_exe_sha256","agent_disk_sha256","agent_disk_dev","agent_disk_inode","unit","state_db_path_class","state_db_dev","state_db_inode","quick_check","local_global_revision","artifact_url_present","artifact_url_sha256","artifact_url_origin_strict_repair_18080","artifact_url_path_class","raw_url_emitted","config_env_or_definition_emitted"}
    return {k:out[k] for k in allowed if k in out}

def main():
    source=HELPER.read_bytes()
    if digest(source)!=HELPER_SHA: raise SafeStop("helper_source_sha_mismatch")
    compile(source.decode(),str(HELPER),"exec")
    if len(source)>65536: raise SafeStop("helper_source_size_limit")
    base,token=read_private(); api=API(base,token)
    nonce=uuid.uuid4().hex[:12]; name="qa-artifact-url-probe-"+nonce
    outdir=HERE/"attempt01"
    if outdir.exists() or outdir.is_symlink(): raise SafeStop("evidence_directory_collision")
    outdir.mkdir(mode=0o700)
    result={"status":"PREFLIGHT","grant_id":"ROOT-NATIVE1-ARTIFACT-URL-READONLY-20261012-01","nonce":nonce,"task_name":name,
            "helper_sha256":HELPER_SHA,"authority":"repair:18080","api_responses":[],"task_create_posts":0,"run_posts":0,
            "task_delete_http":None,"task_get_404":False,"execution_terminal_proven":False,"secret_values_recorded":False}
    ledger={"nonce":nonce,"task_name":name,"helper_sha256":HELPER_SHA,"node_id":NODE,"agent_id":AGENT,"app_id":APP,
            "task_id":None,"execution_id":None,"run_post_attempted":False,"terminal_proven":False,"task_deleted_404":False}
    progress={"updated_at_utc":utc(),"status":"PREFLIGHT","next_action":"read exact Native1 Node/App assignment and reject any identity or name mismatch"}
    save(outdir/"result.json",result); save(outdir/"ledger.json",ledger); save(outdir/"progress.json",progress)
    task_id=None; execution_id=None; command_sha=None; run_attempted=False; terminal=False
    try:
        path="/api/me"; code,me,meta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",code,meta))
        if code!=200 or not isinstance(me,dict) or me.get("Role")!="administrator": raise SafeStop("private_api_session_guard_failed")
        result["api_role"]="administrator"
        path="/api/nodes/"+NODE; code,node,meta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",code,meta))
        if code!=200 or not isinstance(node,dict) or node.get("id")!=NODE or node.get("agent_id")!=AGENT or node.get("status")!="online" or node.get("sync_status")!="synced" or node.get("deployment_mode")!="native" or node.get("host_integration") is not False:
            raise SafeStop("fresh_native1_identity_health_guard_failed")
        result["node_preflight"]={"node_id":NODE,"agent_id":AGENT,"online_synced":True,"deployment_mode":"native","host_integration":False}
        path="/api/applications/"+APP+"/nodes"; code,assigned,meta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",code,meta))
        if code!=200 or assigned!=[NODE]: raise SafeStop("exact_app_assignment_guard_failed")
        result["app_assignment_node_ids"]=[NODE]
        path="/api/tasks"; code,obj,meta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",code,meta)); task_rows=rows(obj,("tasks","items"))
        if code!=200 or task_rows is None or any(isinstance(x,dict) and x.get("name")==name for x in task_rows): raise SafeStop("unique_task_name_guard_failed")
        phase(outdir,"PREFLIGHT_PASS","create one retry0 timeout15 Native1 Command Task and verify exact readback")
        command="python3 -c "+shlex.quote(source.decode())
        command_sha=digest(command.encode()); body={"name":name,"type":"command","command":command,"target":{"type":"node","node_ids":[NODE]},"parameters":[],"timeout":15,"retry":0,"offline_policy":"hub_online_required","enabled":True}
        ledger["task_create_requested"]=True; save(outdir/"ledger.json",ledger)
        path="/api/tasks"; result["task_create_posts"]+=1; code,created,meta=api.request("POST",path,body); result["api_responses"].append(safe_response(path,"POST",code,meta)); save(outdir/"result.json",result)
        if code not in (200,201) or not isinstance(created,dict) or not isinstance(created.get("id"),str):
            raise SafeStop("task_create_response_unconfirmed")
        task_id=created["id"]; ledger["task_id"]=task_id; ledger["command_sha256"]=command_sha; save(outdir/"ledger.json",ledger)
        path="/api/tasks/"+task_id; code,task,meta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",code,meta))
        target=task.get("target",{}) if isinstance(task,dict) else {}
        if (code!=200 or task.get("id")!=task_id or task.get("name")!=name or task.get("type")!="command" or digest(str(task.get("command","")).encode())!=command_sha
                or target.get("node_ids")!=[NODE] or task.get("timeout")!=15 or task.get("retry")!=0 or task.get("offline_policy")!="hub_online_required"):
            raise SafeStop("exact_task_readback_guard_failed")
        result["task_readback"]={"id":task_id,"name":name,"type":"command","target_node_ids":[NODE],"timeout":15,"retry":0,"command_sha256_match":True}
        phase(outdir,"TASK_READBACK_PASS","verify no Schedule reference, then submit the one Run POST")
        path="/api/schedules"; code,schedule_obj,meta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",code,meta)); schedule_rows=rows(schedule_obj,("schedules","items"))
        if code!=200 or schedule_rows is None or any(isinstance(s,dict) and s.get("task_id")==task_id for s in schedule_rows): raise SafeStop("unexpected_schedule_reference")
        result["schedule_reference_count"]=0
        path="/api/nodes/"+NODE; code,node,meta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",code,meta))
        if code!=200 or not isinstance(node,dict) or node.get("id")!=NODE or node.get("agent_id")!=AGENT or node.get("status")!="online" or node.get("sync_status")!="synced": raise SafeStop("pre_run_native1_guard_failed")
        ledger["run_post_attempted"]=True; save(outdir/"ledger.json",ledger); run_attempted=True
        phase(outdir,"RUN_REQUESTED","reconcile exact Execution and wait for terminal status")
        path="/api/tasks/"+task_id+"/run"; result["run_posts"]+=1; code,started,meta=api.request("POST",path,{"params":{}}); result["api_responses"].append(safe_response(path,"POST",code,meta)); save(outdir/"result.json",result)
        execs=rows(started,("executions",))
        if execs is None and isinstance(started,dict) and isinstance(started.get("id"),str): execs=[started]
        if isinstance(execs,list) and len(execs)==1 and isinstance(execs[0],dict) and isinstance(execs[0].get("id"),str): execution_id=execs[0]["id"]
        else:
            for _ in range(10):
                q="/api/executions?"+urlencode({"task_id":task_id,"limit":10}); qc,qobj,qmeta=api.request("GET",q); result["api_responses"].append(safe_response(q,"GET",qc,qmeta))
                found=rows(qobj,("executions","items")) if qc==200 else None
                if found is not None:
                    matches=[e for e in found if isinstance(e,dict) and e.get("task_id")==task_id]
                    if len(matches)==1 and isinstance(matches[0].get("id"),str): execution_id=matches[0]["id"]; break
                    if len(matches)>1: raise SafeStop("execution_reconciliation_ambiguous")
                time.sleep(.25)
        if not execution_id:
            ledger["execution_reconcile"]="no_unique_execution_id"; save(outdir/"ledger.json",ledger)
            raise SafeStop("execution_id_unconfirmed_task_retained")
        ledger["execution_id"]=execution_id; save(outdir/"ledger.json",ledger)
        deadline=time.monotonic()+40; ex=None
        while time.monotonic()<deadline:
            path="/api/executions/"+execution_id; ec,ex,emeta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",ec,emeta))
            if ec==200 and isinstance(ex,dict) and ex.get("id")==execution_id and ex.get("task_id")==task_id and ex.get("node_id")==NODE and ex.get("status") in TERMINAL:
                terminal=True; break
            if ec!=200: raise SafeStop("exact_execution_readback_failed")
            time.sleep(.35)
        if not terminal:
            path="/api/executions/"+execution_id+"/cancel"; cc,cobj,cmeta=api.request("POST",path,{}); result["api_responses"].append(safe_response(path,"POST",cc,cmeta)); result["cancel_post_count"]=1; save(outdir/"result.json",result)
            cancel_deadline=time.monotonic()+25
            while time.monotonic()<cancel_deadline:
                path="/api/executions/"+execution_id; ec,ex,emeta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",ec,emeta))
                if ec==200 and isinstance(ex,dict) and ex.get("id")==execution_id and ex.get("task_id")==task_id and ex.get("node_id")==NODE and ex.get("status") in TERMINAL:
                    terminal=True; break
                if ec!=200: raise SafeStop("cancel_terminal_readback_failed")
                time.sleep(.35)
        ledger["terminal_proven"]=terminal; save(outdir/"ledger.json",ledger)
        if not terminal: raise SafeStop("exact_execution_still_active_task_retained")
        path="/api/executions/"+execution_id+"/logs"; lc,logs,lmeta=api.request("GET",path,limit=1024*1024); result["api_responses"].append(safe_response(path,"GET",lc,lmeta))
        chunks=rows(logs,("logs",)) if lc==200 else None
        if chunks is None: raise SafeStop("execution_log_readback_failed")
        stdout="".join(x.get("chunk","") for x in sorted((x for x in chunks if isinstance(x,dict) and x.get("stream")=="stdout"),key=lambda x:(x.get("seq",0),x.get("created_at",""))) if isinstance(x.get("chunk"),str))
        try: helper=json.loads(stdout) if stdout else None
        except ValueError: helper=None
        result["execution"]={"id":execution_id,"task_id":task_id,"node_id":NODE,"status":ex.get("status"),"task_revision":ex.get("task_revision"),"terminal_proven":True}
        result["stdout"]={"bytes":len(stdout.encode()),"sha256":digest(stdout.encode()),"json_parse_ok":isinstance(helper,dict),"raw_recorded":False}
        result["helper_result"]=safe_helper(helper) if isinstance(helper,dict) else None
        result["status"]="HELPER_EXECUTION_TERMINAL"
    except SafeStop as exc:
        result["status"]="SAFE_STOP"; result["safe_error"]=str(exc)
    except Exception as exc:
        result["status"]="SAFE_STOP"; result["safe_error_type"]=type(exc).__name__
    finally:
        # Delete the exact helper Task only after proving no Run occurred or its unique Execution is terminal.
        if not task_id:
            path="/api/tasks"; code,obj,meta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",code,meta))
            ts=rows(obj,("tasks","items")) if code==200 else None
            matches=[t for t in (ts or []) if isinstance(t,dict) and t.get("name")==name and command_sha is not None and digest(str(t.get("command","")).encode())==command_sha and t.get("target",{}).get("node_ids")==[NODE]] if ts is not None else []
            if len(matches)==1: task_id=matches[0].get("id"); ledger["task_id"]=task_id
            elif len(matches)>1: result["cleanup_status"]="AMBIGUOUS_TASK_RETAINED"
        if task_id and (not run_attempted or terminal):
            path="/api/tasks/"+task_id; code,task,meta=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",code,meta))
            expected=(code==200 and isinstance(task,dict) and task.get("id")==task_id and task.get("name")==name and task.get("type")=="command"
                      and command_sha is not None and digest(str(task.get("command","")).encode())==command_sha and task.get("target",{}).get("node_ids")==[NODE] and task.get("retry")==0 and task.get("timeout")==15)
            if expected:
                path="/api/tasks/"+task_id; dc,_,dm=api.request("DELETE",path); result["api_responses"].append(safe_response(path,"DELETE",dc,dm)); result["task_delete_http"]=dc
            elif code==404: result["task_delete_http"]=404
            else: result["cleanup_status"]="IDENTITY_GUARD_FAILED_TASK_RETAINED"
            path="/api/tasks/"+task_id; gc,_,gm=api.request("GET",path); result["api_responses"].append(safe_response(path,"GET",gc,gm)); result["task_get_404"]=gc==404
            if gc!=404: result["cleanup_status"]="TASK_NOT_404"
        elif task_id:
            result["cleanup_status"]="ACTIVE_OR_UNRESOLVED_EXECUTION_TASK_RETAINED"
        result["task_id"]=task_id; result["execution_id"]=execution_id; result["execution_terminal_proven"]=terminal
        result["updated_at_utc"]=utc(); ledger.update(task_id=task_id,execution_id=execution_id,terminal_proven=terminal,task_deleted_404=result.get("task_get_404",False))
        save(outdir/"ledger.json",ledger); save(outdir/"result.json",result)
        save(outdir/"progress.json",{"updated_at_utc":utc(),"status":result["status"],"next_action":"Root review; preserve Execution history; no rerun","task_get_404":result.get("task_get_404",False)})
    return 0 if terminal and result.get("task_get_404") else 1

if __name__=="__main__": raise SystemExit(main())
