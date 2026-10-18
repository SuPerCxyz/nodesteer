#!/usr/bin/env python3
"""Offline controller call-chain smoke using real outer.run_helper and a FakeAPI."""
import contextlib
import ast
import hashlib
import importlib.util
import io
import json
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
CONTROLLER = HERE / "artifact_cache_controller.py"
LIFECYCLE = HERE / "artifact_cache_lifecycle.py"
ACTIVE_API = None


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def helper_artifact_cache_return_keys():
    tree=ast.parse(LIFECYCLE.read_text())
    functions={node.name:node for node in tree.body if isinstance(node,ast.FunctionDef)}
    def keys_for(function_name):
        function=functions[function_name]
        for node in ast.walk(function):
            if not isinstance(node,ast.Return) or not isinstance(node.value,ast.Dict):
                continue
            for key,value in zip(node.value.keys,node.value.values):
                if isinstance(key,ast.Constant) and key.value=="artifact_cache" and isinstance(value,ast.Dict):
                    return {k.value for k in value.keys if isinstance(k,ast.Constant)}
        raise AssertionError(f"artifact_cache_return_schema_missing:{function_name}")
    a=keys_for("read_cache"); pre_b=keys_for("read_cache_snapshot")
    expected={"sha256","path_relative","path_db","size_bytes","file_dev","file_inode","file_uid","file_gid"}
    assert a==pre_b==expected,(a,pre_b)
    return a


class FakeAPI:
    def __init__(self, controller, root_status="EXACT_OWNED_ROOT_REMOVED", upload_fails=False):
        self.c = controller
        self.root_statuses = [root_status] if isinstance(root_status, str) else list(root_status)
        self.upload_fails = upload_fails
        self.on_response = None
        self.progress_path = None
        self.nonce = None
        self.tasks = {}
        self.executions = {}
        self.logs = {}
        self.nodes = {
            nid: {"id": nid, "hostname": "protected", "agent_id": "protected-agent",
                  "status": "online", "sync_status": "synced", "global_revision": 10,
                  "deployment_mode": "native", "host_integration": True}
            for nid in controller.PROTECTED
        }
        self.artifacts = {}
        self.apps = {}
        self.next_node = 0
        self.next_task = 0
        self.next_execution = 0

    def progress(self, status, next_action):
        return None

    def _meta(self, code, obj):
        raw = json.dumps(obj, sort_keys=True).encode() if obj is not None else b""
        meta = {"status": code, "bytes": len(raw), "json_type": type(obj).__name__ if obj is not None else "empty",
                "body_sha256": hashlib.sha256(raw).hexdigest()}
        if self.on_response:
            self.on_response(meta)
        return code, obj, meta

    def _node(self, node_id):
        return self.nodes.get(node_id)

    def _run_remote(self, command):
        argv = shlex.split(command)
        opts = {}
        for i, value in enumerate(argv[:-1]):
            if value.startswith("--"):
                opts[value] = argv[i + 1]
        phase = opts["--phase"]
        nonce = opts["--nonce"]
        if phase == "prepare":
            self.next_node += 1
            node_id = f"fake-node-{self.next_node}"
            node_name = "qa-artcache-" + nonce
            self.nodes[node_id] = {"id": node_id, "hostname": node_name,
                "agent_id": "fake-agent-" + nonce, "status": "online", "sync_status": "synced",
                "global_revision": 10, "deployment_mode": "docker", "host_integration": False}
            return {"status":"CONTAINER_A_STARTED", "root_dev":7, "root_inode":11,
                    "owner_root_marker_valid":True,"container_a_id":"fake-cid-a",
                    "network_id":"fake-network","credentials_emitted":False}
        if phase == "inspect-a":
            node_id=opts["--node-id"]; aid=opts["--app-id"]; art=opts["--artifact-id"]
            sha=opts["--artifact-sha256"]; agent=self.nodes[node_id]["agent_id"]
            cache_fields={"sha256":sha,"path_relative":"artifacts/"+sha,
                "path_db":"/var/lib/nodesteer/artifacts/"+sha,"size_bytes":32,
                "file_dev":7,"file_inode":17,"file_uid":65534,"file_gid":65534}
            cache_keys=helper_artifact_cache_return_keys()
            db={"db_dev":7,"db_inode":13,"global_revision":10,
                "identity":{"node_id":node_id,"agent_id":agent},
                "application":{"id":aid,"revision":1,"artifact_id":art},
                "artifact_cache":{key:cache_fields[key] for key in cache_keys}}
            self._db=db
            return {"status":"ARTIFACT_CACHE_A_VERIFIED","local_database":db,"credentials_emitted":False}
        if phase == "recreate-b":
            return {"status":"CONTAINER_B_STARTED","container_a_removed_id":"fake-cid-a",
                    "pre_b_persistence":self._db,"credentials_emitted":False}
        if phase == "inspect-b":
            return {"status":"ARTIFACT_CACHE_PERSISTED_A_TO_B","local_database":self._db,
                    "credentials_emitted":False}
        if phase == "cleanup-runtime":
            return {"status":"RUNTIME_REMOVED_ROOT_RETAINED","network_removed":True,"credentials_emitted":False}
        if phase == "cleanup-root":
            status=self.root_statuses.pop(0) if len(self.root_statuses)>1 else self.root_statuses[0]
            return {"status":status,"root_absent":status=="EXACT_OWNED_ROOT_REMOVED",
                    "hub_objects_404_gate":status=="EXACT_OWNED_ROOT_REMOVED","credentials_emitted":False}
        raise AssertionError("unexpected phase")

    def request(self, method, path, body=None, timeout=12, limit=2*1024*1024):
        path=path.split("?",1)[0]
        code,obj=404,None
        if path == "/api/nodes" and method == "GET":
            code,obj=200,{"nodes":list(self.nodes.values())}
        elif path.startswith("/api/nodes/"):
            node_id=path.rsplit("/",1)[1]
            if method == "GET" and node_id in self.nodes: code,obj=200,self.nodes[node_id]
            elif method == "DELETE" and node_id in self.nodes:
                self.nodes.pop(node_id); code,obj=200,{"ok":True}
        elif path == "/api/tasks" and method == "GET":
            code,obj=200,{"tasks":list(self.tasks.values())}
        elif path == "/api/tasks" and method == "POST":
            self.next_task+=1; tid=f"fake-task-{self.next_task}"
            task={"id":tid,**body}; self.tasks[tid]=task; code,obj=200,task
        elif path.startswith("/api/tasks/"):
            parts=path.strip("/").split("/"); tid=parts[2]
            if len(parts)==3 and method=="GET" and tid in self.tasks: code,obj=200,self.tasks[tid]
            elif len(parts)==3 and method=="DELETE" and tid in self.tasks:
                self.tasks.pop(tid); code,obj=200,{"ok":True}
            elif len(parts)==4 and parts[3]=="run" and method=="POST" and tid in self.tasks:
                task=self.tasks[tid]; self.next_execution+=1; eid=f"fake-execution-{self.next_execution}"
                remote=self._run_remote(task["command"])
                ex={"id":eid,"task_id":tid,"status":"SUCCESS"}; self.executions[eid]=ex
                self.logs[eid]={"logs":[{"seq":1,"stream":"stdout","chunk":json.dumps(remote)}]}
                code,obj=200,{"executions":[{"id":eid}]}
        elif path.startswith("/api/executions/"):
            bits=path.strip("/").split("/"); eid=bits[2]
            if len(bits)==3 and method=="GET" and eid in self.executions: code,obj=200,self.executions[eid]
            elif len(bits)==4 and bits[3]=="logs" and method=="GET" and eid in self.logs: code,obj=200,self.logs[eid]
        elif path == "/api/artifacts" and method == "GET":
            code,obj=200,{"artifacts":list(self.artifacts.values())}
        elif path.startswith("/api/artifacts/"):
            aid=path.rsplit("/",1)[1]
            if method=="GET" and aid in self.artifacts: code,obj=200,self.artifacts[aid]
            elif method=="DELETE" and aid in self.artifacts:
                self.artifacts.pop(aid); code,obj=200,{"ok":True}
        elif path == "/api/applications" and method == "GET":
            code,obj=200,{"applications":list(self.apps.values())}
        elif path == "/api/applications" and method == "POST":
            aid=f"fake-app-{len(self.apps)+1}"
            app={"id":aid,**body,"assigned":[]}; self.apps[aid]=app; code,obj=200,app
        elif path.startswith("/api/applications/"):
            bits=path.strip("/").split("/"); aid=bits[2]; app=self.apps.get(aid)
            if len(bits)==3 and method=="GET" and app: code,obj=200,app
            elif len(bits)==3 and method=="DELETE" and app:
                self.apps.pop(aid); code,obj=200,{"ok":True}
            elif len(bits)==4 and app and bits[3]=="assign" and method=="POST":
                node_ids=body["node_ids"]
                app["assigned"]=[] if body.get("remove") else node_ids
                code,obj=200,{"ok":True}
            elif len(bits)==4 and app and bits[3]=="state" and method=="GET": code,obj=200,[]
            elif len(bits)==4 and app and bits[3]=="nodes" and method=="GET": code,obj=200,list(app["assigned"])
            elif len(bits)==4 and app and bits[3]=="executions" and method=="GET": code,obj=200,[]
        return self._meta(code,obj)


def fake_upload(self, payload, name, version):
    if ACTIVE_API.upload_fails:
        raise RuntimeError("offline_fake_upload_failure")
    aid="fake-artifact-1"
    artifact={"id":aid,"name":name,"version":version,"sha256":hashlib.sha256(payload).hexdigest(),"size":len(payload)}
    ACTIVE_API.artifacts[aid]=artifact
    return artifact


def controller_smoke():
    controller=load(CONTROLLER,"sys12_smoke_controller")
    outer=controller.load_outer()
    temp=Path(tempfile.mkdtemp(prefix="controller-smoke-",dir=HERE))
    old=(controller.HERE,controller.HELPER,controller.load_outer,
         controller.UploadOpener.upload_artifact,sys.argv)
    active={"api":None}
    global ACTIVE_API
    try:
        controller.HERE=temp; controller.HELPER=LIFECYCLE
        cache_keys=helper_artifact_cache_return_keys()
        assert "path_relative" in cache_keys
        window_writes=[]; window_sleeps=[]
        class WindowApp:
            @staticmethod
            def save(path,value): window_writes.append((str(path),value))
            @staticmethod
            def utc(): return "LOCAL_FAKE_UTC"
        controller.readonly_node_observation_window(WindowApp(),temp/"progress.json","a1b2c3d4e5f6","fake-node",
                                                    sleep=lambda seconds:window_sleeps.append(seconds))
        assert window_sleeps==[1]*30 and len(window_writes)==31
        assert all(x[1]["status"]=="OWNED_NODE_UI_READONLY_WINDOW" and x[1]["node_id"]=="fake-node"
                   and x[1]["nonce"]=="a1b2c3d4e5f6" for x in window_writes)
        assert [x[1]["seconds_remaining"] for x in window_writes]==list(range(30,0,-1))+[0]
        owned=temp/"owned-evidence.json"
        owned.write_text('{"safe":true}'); owned.chmod(0o600)
        assert controller.read_owned_evidence(owned)=={"safe":True}
        bad_mode=temp/"bad-mode.json"; bad_mode.write_text('{}'); bad_mode.chmod(0o644)
        try: controller.read_owned_evidence(bad_mode)
        except RuntimeError as exc: assert str(exc)=="recovery_evidence_file_guard_failed"
        else: raise AssertionError("read_owned_evidence accepted wrong mode")
        symlink=temp/"evidence-link.json"; symlink.symlink_to(owned)
        try: controller.read_owned_evidence(symlink)
        except RuntimeError as exc: assert str(exc)=="recovery_evidence_file_guard_failed"
        else: raise AssertionError("read_owned_evidence followed symlink")
        nested=temp/"nested"; nested.mkdir(); nested_file=nested/"evidence.json"
        nested_file.write_text('{}'); nested_file.chmod(0o600)
        try: controller.read_owned_evidence(nested_file)
        except RuntimeError as exc: assert str(exc)=="recovery_evidence_path_scope_invalid"
        else: raise AssertionError("read_owned_evidence accepted subdirectory path")
        def load_fake_outer():
            global ACTIVE_API
            outer.HERE=temp
            outer.read_private=lambda _path:("http://192.168.100.209:18080","local-fake-token")
            def make_api(_base,_token):
                global ACTIVE_API
                active["api"]=FakeAPI(controller)
                ACTIVE_API=active["api"]
                return ACTIVE_API
            outer.API=make_api
            return outer
        controller.load_outer=load_fake_outer
        controller.UploadOpener.upload_artifact=fake_upload
        sys.argv=[str(CONTROLLER),"--execute","--grant-id","FAKE-ONLY",
                  "--approved-helper-sha256",hashlib.sha256(LIFECYCLE.read_bytes()).hexdigest(),
                  "--approved-controller-sha256",hashlib.sha256(CONTROLLER.read_bytes()).hexdigest(),
                  "--connection","ignored-private-path"]
        controller.main()
        summaries=list(temp.glob("artifact-cache-*-result.json"))
        assert len(summaries)==1
        result=json.loads(summaries[0].read_text())
        assert result["status"]=="SYS12_ARTIFACT_CACHE_A_TO_B_PASS"
        assert len(result["helper_results"])==6
        assert [x["phase"] for x in result["helper_results"]]==[
            "prepare","inspect-a","recreate-b","inspect-b","cleanup-runtime","cleanup-root"]
        assert len({x["execution_id"] for x in result["helper_results"]})==6
        assert all(x["status"]=="SUCCESS" and x["remote"]["credentials_emitted"] is False
                   for x in result["helper_results"])
        led=json.loads(next(temp.glob("artifact-cache-*-ledger.json")).read_text())
        assert led["helper_call_count_total"]==6 and led["helper_call_count_normal"]==6
        assert led["phase"]=="COMPLETE" and led["api_objects_deleted"] is True
        assert not active["api"].tasks and not active["api"].nodes.keys() - set(controller.PROTECTED)

        # Run another main flow that stops after prepare, then exercise true recover() via main.
        failed_api=FakeAPI(controller,upload_fails=True)
        def load_failed_outer():
            global ACTIVE_API
            outer.HERE=temp
            outer.read_private=lambda _path:("http://192.168.100.209:18080","local-fake-token")
            def make_api(_base,_token):
                global ACTIVE_API
                active["api"]=failed_api
                ACTIVE_API=failed_api
                return failed_api
            outer.API=make_api
            ACTIVE_API=failed_api
            return outer
        controller.load_outer=load_failed_outer
        with contextlib.redirect_stdout(io.StringIO()):
            try: controller.main()
            except RuntimeError as exc: assert str(exc)=="offline_fake_upload_failure"
            else: raise AssertionError("prepare-only failure injection did not stop main")
        ledger_path=next(p for p in temp.glob("artifact-cache-*-ledger.json")
                         if json.loads(p.read_text()).get("status")=="FAILED_OR_PARTIAL")
        first_result=next(temp.glob(ledger_path.name.replace("-ledger.json","-result.json")))
        ledger=json.loads(ledger_path.read_text())
        assert len(ledger["helper_calls"])==1 and ledger["helper_calls"][0]["phase"]=="prepare"
        assert len(json.loads(first_result.read_text())["helper_results"])==1

        # A nonterminal cleanup result must leave RECOVERY_PARTIAL, then an exact result closes it.
        failed_api.root_statuses=["SAFE_STOP","EXACT_OWNED_ROOT_REMOVED"]
        ledger["api_objects_deleted"]=False
        # recovery deletes the node exact-ID first; node cleanup is already represented by ledger.
        def invoke_recovery():
            controller.load_outer=load_failed_outer
            sys.argv=[str(CONTROLLER),"--execute","--grant-id","FAKE-RECOVERY",
                      "--approved-helper-sha256",hashlib.sha256(LIFECYCLE.read_bytes()).hexdigest(),
                      "--approved-controller-sha256",hashlib.sha256(CONTROLLER.read_bytes()).hexdigest(),
                      "--connection","ignored-private-path","--recover-ledger",str(ledger_path)]
            try: controller.main()
            except RuntimeError as exc: return str(exc)
            return None
        first_error=invoke_recovery()
        assert first_error=="recovery_root_cleanup_not_complete", first_error
        ledger=json.loads(ledger_path.read_text()); recovered=json.loads(first_result.read_text())
        assert ledger["status"]=="RECOVERY_PARTIAL" and ledger["phase"]!="RECOVERY_COMPLETE"
        assert recovered["status"]=="RECOVERY_PARTIAL"
        assert [x["phase"] for x in recovered["helper_results"]]==["prepare","cleanup-runtime","cleanup-root"]
        assert recovered["helper_results"][-1]["remote"]["status"]=="SAFE_STOP"
        assert not failed_api.nodes.keys() - set(controller.PROTECTED)

        assert invoke_recovery() is None
        ledger=json.loads(ledger_path.read_text()); recovered=json.loads(first_result.read_text())
        assert ledger["status"]=="RECOVERY_COMPLETE" and ledger["phase"]=="RECOVERY_COMPLETE"
        assert len(recovered["helper_results"])==5
        assert [x["phase"] for x in recovered["helper_results"]]==[
            "prepare","cleanup-runtime","cleanup-root","cleanup-runtime","cleanup-root"]
        assert recovered["helper_results"][-1]["remote"]["status"]=="EXACT_OWNED_ROOT_REMOVED"
        assert ledger["helper_call_count_recovery"]==4
        return {"normal_main":"PASS","normal_helper_calls":6,"recovery_main_partial":"PASS",
                "recovery_root_status_guard":"PASS","recovery_retry_closure":"PASS",
                "duplicate_result_appends":False,"read_owned_evidence_guards":"PASS",
                "actual_helper_ast_cache_keysets_equal":"PASS","observation_window_30_one_second_steps":"PASS",
                "fakeapi_only":True}
    finally:
        (controller.HERE,controller.HELPER,controller.load_outer,
         controller.UploadOpener.upload_artifact,sys.argv)=old
        shutil.rmtree(temp)


def cleanup_source_smoke():
    helper=load(LIFECYCLE,"sys12_smoke_lifecycle")
    # The helper's existing-ID guard must reject a still-present exact container ID.
    with patch.object(helper,"docker",return_value=(0,'[{"Id":"known-cid"}]','')):
        try: helper.exact_absent("container","known-cid")
        except helper.SafeStop as exc: assert str(exc)=="exact_resource_name_collision"
        else: raise AssertionError("existing container ID was not rejected")
    with (patch.object(helper,"docker",return_value=(0,'[{"Id":"known-cid"}]','')),
          patch.object(helper,"inspect_container",return_value={"running":False})):
        try: helper.require_known_container_absent({},"known-cid","owned-a",None,None)
        except helper.SafeStop as exc: assert str(exc)=="known_removed_container_id_still_present"
        else: raise AssertionError("existing known container ID was accepted as absent")

    cache_payload=b"x"*32
    sha=hashlib.sha256(cache_payload).hexdigest()
    def exercise_case(case, should_complete):
        temp=Path(tempfile.mkdtemp(prefix="cleanup-smoke-",dir=HERE))
        root=temp/"qa-resume-20261012-artcache-abc123abc123"; root.mkdir(mode=0o700)
        private=root/"private"; private.mkdir(); data=root/"data"; data.mkdir()
        root.chmod(0o700); private.chmod(0o700); data.chmod(0o700)
        config=private/"agent.yaml"; config.write_text("dummy-no-secret"); config.chmod(0o600)
        db=data/"state.db"; db.write_bytes(b"db"); db.chmod(0o644)
        for name in ("scripts","applications","executions","logs","tmp","artifacts"):
            (data/name).mkdir(); (data/name).chmod(0o755)
        if case=="nonempty": (data/"scripts"/"x").write_text("x")
        if case=="symlink":
            shutil.rmtree(data/"scripts"); (data/"scripts").symlink_to(temp,target_is_directory=True)
        if case=="unknown": (data/"unexpected").write_text("x")
        if case=="approved-cache": (data/"artifacts"/sha).write_bytes(cache_payload)
        root_st=root.stat(); net="nodesteer-qa-artcache-abc123abc123"
        owner="qa-resume-20261012-artcache-abc123abc123"
        marker={"owner":owner,"nonce":"abc123abc123","root":str(root),"root_dev":root_st.st_dev,
                "root_inode":root_st.st_ino,"image_id":helper.IMAGE,"network_name":net,
                "artifact_sha256":sha,"container_a_id":None,"container_b_id":None,
                "network_id":None,"network_removed_id":"absent","created_container_ids":[]}
        ledger={**{k:marker[k] for k in ("owner","nonce","root","root_dev","root_inode","image_id","network_name")},
                "phase":"RUNTIME_REMOVED_ROOT_RETAINED","removed_container_ids":[]}
        (root/"owner.json").write_text("{}"); (root/"resource-ledger.json").write_text("{}")
        orig_lstat=Path.lstat
        def fake_lstat(p):
            st=orig_lstat(p)
            try: rel=p.relative_to(root)
            except ValueError: return st
            if stat.S_ISLNK(st.st_mode): return st
            wanted_uid,wanted_gid,wanted_mode=0,0,0o600
            if rel==Path("."):
                wanted_uid,wanted_gid,wanted_mode=0,0,0o700
            elif rel==Path("private") or rel==Path("data"):
                wanted_uid,wanted_gid,wanted_mode=65534,65534,0o700
            elif rel.parts and rel.parts[0]=="data" and len(rel.parts)==2 and rel.name!="state.db":
                wanted_uid,wanted_gid,wanted_mode=65534,65534,0o755
            elif rel==Path("private/agent.yaml") or (rel.parts and rel.parts[0]=="data"):
                wanted_uid,wanted_gid,wanted_mode=65534,65534,0o600 if rel.name=="agent.yaml" else 0o644
            return SimpleNamespace(st_mode=(st.st_mode & ~0o7777) | wanted_mode, st_ino=st.st_ino,
                                   st_dev=st.st_dev, st_nlink=st.st_nlink, st_uid=wanted_uid,
                                   st_gid=wanted_gid, st_size=st.st_size, st_atime=st.st_atime,
                                   st_mtime=st.st_mtime, st_ctime=st.st_ctime)
        current_marker=[marker]
        def fake_owned_json(path, *_args):
            obj=current_marker[0] if Path(path).name=="owner.json" else ledger
            return orig_lstat(Path(path)),json.dumps(obj).encode()
        def fake_atomic_owner(_root,m,l): current_marker[0]=m; ledger.update(l)
        def fake_atomic_json(path,obj,*_args):
            if Path(path).name=="owner.json": current_marker[0]=obj
            else: ledger.update(obj)
        def patched_names(nonce): return {"owner":owner,"root":root,"nonce":nonce,"network":net,"a":owner+"-a","b":owner+"-b"}
        try:
            with patch.object(helper,"resource_names",patched_names), patch.object(helper,"docker",side_effect=lambda argv,**_kw:(1,"",f"No such {argv[0]}: {argv[-1]}")), \
                 patch.object(helper,"owned_json",fake_owned_json), patch.object(helper,"atomic_owner",fake_atomic_owner), \
                 patch.object(helper,"atomic_json",fake_atomic_json), \
                 patch.object(Path,"lstat",fake_lstat):
                output=io.StringIO()
                with contextlib.redirect_stdout(output):
                    try:
                        helper.phase_cleanup_root("abc123abc123",True,sha,root_st.st_dev,root_st.st_ino)
                    except helper.SafeStop:
                        pass
                returned=json.loads(output.getvalue()) if output.getvalue() else {"status":"SAFE_STOP"}
                status=returned["status"]
            if should_complete:
                assert status=="EXACT_OWNED_ROOT_REMOVED" and returned.get("root_absent") is True and not root.exists()
            else:
                assert status=="SAFE_STOP" and root.exists()
                assert (root/"owner.json").exists() and (root/"resource-ledger.json").exists()
                assert (root/"data"/"state.db").exists()
                assert (root/"data"/"scripts").exists() or (root/"data"/"scripts").is_symlink()
            return status
        finally: shutil.rmtree(temp)
    assert exercise_case("clean",True)=="EXACT_OWNED_ROOT_REMOVED"
    assert exercise_case("approved-cache",True)=="EXACT_OWNED_ROOT_REMOVED"
    for case in ("nonempty","symlink","unknown"):
        assert exercise_case(case,False)=="SAFE_STOP"
    return {"existing_cid_rejected":True,"init_dirs_cleaned":True,"approved_cache_cleaned":True,
            "nonempty_symlink_unknown_rejected":True}


if __name__=="__main__":
    print(json.dumps({"controller":controller_smoke(),"cleanup":cleanup_source_smoke(),
                      "classification":"LOCAL_FAKEAPI_QA_HARNESS_SMOKE_NOT_E2E"},sort_keys=True))
