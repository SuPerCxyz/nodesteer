#!/usr/bin/env python3
"""Dynamically revalidate the current owned child and proxy on native4."""
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
import test_api as api

LEDGER_PATH = HERE / "owned-process-ledger.json"
OLD_LEDGER = HERE.parent / "qa-resume-20261008-backend/cases/owned-process-ledger.json"
OLD_SETUP = HERE.parent / "qa-resume-20261008-backend/setup/setup-summary.json"
BUILD = HERE.parent / "build-manifest.json"
ledger = json.loads(LEDGER_PATH.read_text())
old = json.loads(OLD_LEDGER.read_text())
setup = json.loads(OLD_SETUP.read_text())
build = json.loads(BUILD.read_text())
if ledger.get("status") != "PASS" or ledger.get("controls_empty") is not True:
    raise SystemExit("current_owned_ledger_not_ready")
agent_sha = next((x.get("sha256") for x in build.get("artifacts", []) if x.get("name") == "nodesteer-agent-static"), None)
child = ledger["child"]
supervisor = ledger["supervisor"]
if child.get("exe_sha256") != agent_sha or not re.fullmatch(r"[0-9a-f]{64}", str(agent_sha)):
    raise SystemExit("child_sha_disagrees_with_build_manifest")
for p in [child, supervisor]:
    if not isinstance(p.get("pid"), int) or p["pid"] <= 1 or not isinstance(p.get("starttime"), int) or p["starttime"] <= 0 or p.get("uid") != 65534 or not re.fullmatch(r"[0-9a-f]{64}", str(p.get("exe_sha256"))):
        raise SystemExit("invalid_process_claim_in_current_ledger")
owner_dir = str(Path(old["child"]["exe_path"]).parent)
if not owner_dir.startswith("/tmp/qa-resume-20261008-child-"):
    raise SystemExit("owner_dir_prefix_gate_failed")
claims = {
    "node_id": ledger["node_id"],
    "agent_id": ledger["agent_id"],
    "owner_dir": owner_dir,
    "manifest_sha256": ledger["owner_manifest_sha256"],
    "child": {k: child[k] for k in ("pid", "starttime", "uid", "exe_sha256")},
    "supervisor": {k: supervisor[k] for k in ("pid", "starttime", "uid", "exe_sha256")},
    "proxy": ledger["proxy"],
    "agent_build_sha256": agent_sha,
}
source = r'''import hashlib,json,os,socket,stat
from pathlib import Path
C=json.loads(%r); D=Path(C["owner_dir"]); M=D/"owner-manifest.json"
def sha(p):
 h=hashlib.sha256()
 with open(p,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""): h.update(b)
 return h.hexdigest()
def proc(pid,want):
 raw=Path("/proc",str(pid),"stat").read_text(); start=int(raw[raw.rfind(")")+2:].split()[19])
 status=Path("/proc",str(pid),"status").read_text().splitlines(); uid=tuple(map(int,next(x for x in status if x.startswith("Uid:")).split()[1:3]))
 exe=os.readlink("/proc/"+str(pid)+"/exe"); args=[x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(b"\0") if x]
 return {"pid":pid,"starttime":start,"uid":uid[0],"uid_matches":uid==(want["uid"],want["uid"]),"exe_path":exe,"exe_sha256":sha(exe),"exe_sha_matches":sha(exe)==want["exe_sha256"],"starttime_matches":start==want["starttime"],"argv_owner_path_matches":any(x==str(D) or x.startswith(str(D)+os.sep) for x in args)}
st=D.lstat(); raw=M.read_bytes(); m=json.loads(raw)
if stat.S_ISLNK(st.st_mode) or not D.resolve().is_relative_to(Path("/tmp").resolve()) or st.st_uid!=65534: raise RuntimeError("owner_directory_identity_failed")
if hashlib.sha256(raw).hexdigest()!=C["manifest_sha256"] or m.get("node_id")!=C["node_id"] or m.get("agent_id")!=C["agent_id"]: raise RuntimeError("owner_manifest_identity_failed")
if (st.st_dev,st.st_ino)!=(m.get("directory_dev"),m.get("directory_ino")): raise RuntimeError("owner_directory_inode_failed")
child=proc(C["child"]["pid"],C["child"]); supervisor=proc(C["supervisor"]["pid"],C["supervisor"])
ip=C["proxy"]["ip"]; port=C["proxy"]["port"]; iphex=socket.inet_aton(ip)[::-1].hex().upper(); porthex=f"{port:04X}"
listeners=[]
for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
 f=line.split()
 if f[1]==iphex+":"+porthex and f[3]=="0A": listeners.append(f[9])
fds={os.readlink(p) for p in Path("/proc",str(C["supervisor"]["pid"]),"fd").iterdir() if p.is_symlink()}
owned=any("socket:["+i+"]" in fds for i in listeners)
controls=json.loads((D/"controls.json").read_text())
result={"status":"PASS","owner_dir":str(D),"directory":{"dev":st.st_dev,"ino":st.st_ino,"uid":st.st_uid,"mode":oct(stat.S_IMODE(st.st_mode))},"manifest_sha256":hashlib.sha256(raw).hexdigest(),"node_id":m.get("node_id"),"agent_id":m.get("agent_id"),"child":child,"supervisor":supervisor,"proxy":{"ip":ip,"port":port,"listening":bool(listeners),"owned_by_supervisor":owned},"controls_empty":controls=={}}
print(json.dumps(result,separators=(",",":")))
''' % json.dumps(claims, separators=(",", ":"))
native4_id = setup.get("target_native4", {}).get("node_id")
if not native4_id:
    raise SystemExit("native4_identity_missing_from_setup_evidence")
result = api.native4_command("qa-resume-20261009-owned-process-ledger-refresh", source, native4_id, timeout=45)
value = result.get("output")
if result.get("status") != "SUCCESS" or not isinstance(value, dict) or value.get("status") != "PASS":
    raise SystemExit("runtime_owned_process_refresh_failed")
for role in ("child", "supervisor"):
    if not all(value[role].get(k) is True for k in ("uid_matches", "exe_sha_matches", "starttime_matches", "argv_owner_path_matches")):
        raise SystemExit("runtime_process_identity_mismatch")
if not value.get("proxy", {}).get("listening") or not value.get("proxy", {}).get("owned_by_supervisor") or not value.get("controls_empty"):
    raise SystemExit("runtime_proxy_or_controls_mismatch")
ledger.update({"owner_dir": value["owner_dir"], "directory": value["directory"], "child": value["child"], "supervisor": value["supervisor"], "proxy": value["proxy"], "controls_empty": value["controls_empty"], "refresh_task_id": result["task_id"], "refresh_execution_id": result["execution_id"], "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "build_manifest_agent_sha256": agent_sha})
api.write_json(LEDGER_PATH, ledger)
print(json.dumps({"status": "PASS", "node_id": ledger["node_id"], "child_pid": ledger["child"]["pid"], "supervisor_pid": ledger["supervisor"]["pid"], "proxy_port": ledger["proxy"]["port"], "owner_dir": ledger["owner_dir"], "task_id": result["task_id"], "execution_id": result["execution_id"]}))
