#!/usr/bin/env python3
"""Local-only API dependency smoke; all HTTP and helper calls are blocked/stubbed."""
import importlib.util
import inspect
import tempfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
OUTER_PATH=HERE.parent/"sys12_outer_controller.py"
CONTROLLER_PATH=HERE/"artifact_cache_controller.py"

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

outer=load("outer_dependency_smoke",OUTER_PATH)
controller=load("artifact_controller_dependency_smoke",CONTROLLER_PATH)
required={"get_node":("api","node_id"),"run_helper":("api","source","source_sha","ledger","result","phase","params"),"save":("path","value"),"require":("code","expected","label")}
for name,args in required.items():
    fn=getattr(outer,name,None)
    assert callable(fn),name
    params=tuple(inspect.signature(fn).parameters)
    assert params[:len(args)]==args,(name,params)
assert tuple(inspect.signature(controller.wait_synced).parameters)[:2]==("app","api")
assert tuple(inspect.signature(controller.post_result).parameters)[:2]==("app","api")

node={"id":"owned-node-id","hostname":"qa-artcache-smoke","deployment_mode":"docker",
      "host_integration":False,"status":"online","sync_status":"synced","global_revision":44}
class BlockedAPI:
    def __init__(self): self.calls=[]
    def request(self,method,path,*args,**kwargs):
        self.calls.append((method,path))
        if method!="GET" or path!="/api/nodes/owned-node-id":
            raise AssertionError("blocked unexpected API operation")
        return 200,node,{"status":200}
    def progress(self,*args,**kwargs): raise AssertionError("progress should not be needed in immediate read")
api=BlockedAPI()
readback=controller.wait_synced(outer,api,"owned-node-id","qa-artcache-smoke",44)
assert readback is node and api.calls==[("GET","/api/nodes/owned-node-id")]
seen={}
real_run_helper=outer.run_helper
def fake_run_helper(passed_api,*args,**kwargs):
    seen["api"]=passed_api
    return {"status":"SMOKE"}
outer.run_helper=fake_run_helper
try:
    with tempfile.TemporaryDirectory(prefix="api-di-smoke-",dir=HERE) as tmp:
        result={"helper_results":[]}
        ledger={"result_path":str(Path(tmp)/"result.json")}
        got=controller.post_result(outer,api,"source","sha",ledger,result,"prepare",{})
        assert got=={"status":"SMOKE"} and seen["api"] is api
finally:
    outer.run_helper=real_run_helper
assert api.calls==[("GET","/api/nodes/owned-node-id")]
print("dependency_injection=PASS; outer_signatures=PASS; external_api/helper_writes=BLOCKED")
