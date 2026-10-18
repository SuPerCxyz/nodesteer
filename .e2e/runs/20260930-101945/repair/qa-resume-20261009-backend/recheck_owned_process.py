#!/usr/bin/env python3
"""Recheck the single owned test child through one temporary native4 Task."""
import hashlib
import json
import os
import socket
import stat
import sys
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD_BACKEND = Path(__file__).resolve().parents[1] / "qa-resume-20261008-backend"
sys.path.insert(0, str(OLD_BACKEND))
import run_env01_05 as api

api.PRIVATE = Path("/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json")
api.OUT = HERE
TOKEN = api.read_private()
CHILD = "15961ef7-81b4-43f3-80c8-1b6977ca2aea"
NATIVE4 = "03cb50cc-eab6-4afd-bbe0-f73c4e326773"
OWNER_DIR = "/tmp/qa-resume-20261008-child-ciivjupc"
MANIFEST_SHA = "7a5bf21f70ec2610527f054809f40fbc17bb5dcbfd24f8af804237bc811f7cfc"
CHILD_PID, CHILD_START = 44933, 123677943
SUP_PID, SUP_START = 44932, 123677922
CHILD_SHA = "d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
SUP_SHA = "078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531"
PROXY_IP, PROXY_PORT = "127.69.212.143", 35095


def owned_probe_source():
    return r'''import hashlib,json,os,socket,stat
from pathlib import Path
D=Path("/tmp/qa-resume-20261008-child-ciivjupc")
M=D/"owner-manifest.json"
def sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as f:
  for b in iter(lambda:f.read(1048576),b""): h.update(b)
 return h.hexdigest()
def start(pid):
 raw=Path("/proc",str(pid),"stat").read_text(); tail=raw[raw.rfind(")")+2:].split(); return int(tail[19])
def uid(pid):
 for line in Path("/proc",str(pid),"status").read_text().splitlines():
  if line.startswith("Uid:"): return tuple(map(int,line.split()[1:3]))
 raise RuntimeError("uid_missing")
def argv_ok(pid):
 args=[x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(b"\0") if x]
 return any(x==str(D) or x.startswith(str(D)+os.sep) for x in args)
st=D.lstat(); raw=M.read_bytes(); manifest=json.loads(raw)
if stat.S_ISLNK(st.st_mode) or (st.st_dev,st.st_ino,st.st_uid)!=(33,135,65534): raise RuntimeError("directory_identity_mismatch")
if hashlib.sha256(raw).hexdigest()!="7a5bf21f70ec2610527f054809f40fbc17bb5dcbfd24f8af804237bc811f7cfc": raise RuntimeError("manifest_sha_mismatch")
rows=[]
for role,pid,expected_start,expected_uid,expected_sha in [("child",44933,123677943,65534,"d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"),("supervisor",44932,123677922,65534,"078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531")]:
 exe=os.readlink("/proc/"+str(pid)+"/exe"); actual=sha(exe)
 rows.append({"role":role,"pid":pid,"starttime":start(pid),"uid":uid(pid)[0],"uid_matches":uid(pid)==(expected_uid,expected_uid),"exe_sha256":actual,"exe_sha_matches":actual==expected_sha,"argv_owner_path_matches":argv_ok(pid),"starttime_matches":start(pid)==expected_start})
ip="127.69.212.143"; port=35095; iphex=socket.inet_aton(ip)[::-1].hex().upper(); porthex=f"{port:04X}"
listeners=[]
for line in Path("/proc/net/tcp").read_text().splitlines()[1:]:
 f=line.split()
 if f[1]==iphex+":"+porthex and f[3]=="0A": listeners.append(f[9])
fd_sockets={os.readlink(p) for p in Path("/proc",str(44932),"fd").iterdir() if p.is_symlink()}
port_owned=any("socket:["+inode+"]" in fd_sockets for inode in listeners)
controls=json.loads((D/"controls.json").read_text())
print(json.dumps({"status":"PASS","node_id":manifest.get("node_id"),"agent_id":manifest.get("agent_id"),"directory_mode":oct(stat.S_IMODE(st.st_mode)),"manifest_sha256":hashlib.sha256(raw).hexdigest(),"processes":rows,"proxy":{"ip":ip,"port":port,"listening":bool(listeners),"owned_by_supervisor":port_owned},"controls_empty":controls=={}},separators=(",",":")))
'''


def main():
    api.OUT.mkdir(mode=0o700, parents=True, exist_ok=True)
    name = "qa-resume-20261009-owned-process-probe-" + uuid.uuid4().hex[:8]
    command = api.make_python_command(owned_probe_source())
    if TOKEN in command:
        raise RuntimeError("command_contains_bearer")
    definition = {
        "task": {
            "name": name,
            "type": "command",
            "target": {"type": "node", "node_ids": [NATIVE4]},
            "retry": 0,
            "schedule_present": False,
            "timeout": 45,
        },
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (HERE / "owned-process-probe-definition.json").write_text(json.dumps(definition, indent=2) + "\n")
    os.chmod(HERE / "owned-process-probe-definition.json", 0o600)
    payload = {
        "name": name,
        "type": "command",
        "target": {"type": "node", "node_ids": [NATIVE4]},
        "parameters": [],
        "param_values": {},
        "command": command,
        "timeout": 45,
        "retry": 0,
        "offline_policy": "hub_online_required",
        "enabled": True,
    }
    task_id = None
    result = {}
    try:
        status, raw = api.request(TOKEN, "POST", "/api/tasks", payload)
        if status != 200:
            raise RuntimeError("owned_process_probe_create_http_" + str(status))
        task = json.loads(raw)
        task_id = task.get("id")
        if not task_id:
            raise RuntimeError("owned_process_probe_task_id_missing")
        api.write_json(HERE / "owned-process-probe-created.json", {
            "task_id": task_id,
            "name": name,
            "create_http": status,
            "target_node_id": NATIVE4,
            "retry": 0,
            "schedule_present": False,
            "command_sha256": definition["command_sha256"],
        })
        readback = api.get_json(TOKEN, "/api/tasks/" + task_id)
        if readback.get("command") != command or readback.get("target", {}).get("node_ids") != [NATIVE4] or readback.get("retry") != 0 or readback.get("schedule"):
            raise RuntimeError("owned_process_probe_definition_mismatch")
        status, raw = api.request(TOKEN, "POST", "/api/tasks/" + task_id + "/run", {"params": {}})
        if status != 200:
            raise RuntimeError("owned_process_probe_run_http_" + str(status))
        executions = json.loads(raw)
        if len(executions) != 1 or executions[0].get("node_id") != NATIVE4:
            raise RuntimeError("owned_process_probe_run_target_mismatch")
        execution_id = executions[0].get("id")
        if not execution_id:
            raise RuntimeError("owned_process_probe_execution_id_missing")
        deadline = time.monotonic() + 75
        execution = None
        while time.monotonic() < deadline:
            execution = api.get_json(TOKEN, "/api/executions/" + execution_id)
            if execution.get("status") not in {"PENDING", "RUNNING"}:
                break
            time.sleep(0.5)
        if not execution or execution.get("status") in {"PENDING", "RUNNING"}:
            raise RuntimeError("owned_process_probe_execution_timeout")
        output = None
        try:
            output = json.loads(execution.get("stdout") or "")
        except (ValueError, TypeError):
            pass
        result = {"task_id": task_id, "execution_id": execution_id, "status": execution.get("status"), "output": output}
        api.write_json(HERE / "owned-process-probe-execution.json", {
            "task_id": task_id,
            "execution_id": execution_id,
            "node_id": execution.get("node_id"),
            "status": execution.get("status"),
            "exit_code": execution.get("exit_code"),
            "stdout_chars": len(execution.get("stdout") or ""),
            "stderr_chars": len(execution.get("stderr") or ""),
            "output": output,
        })
        output = result.get("output")
        if result.get("status") != "SUCCESS" or not isinstance(output, dict):
            raise RuntimeError("owned_process_probe_task_failed")
        expected = output.get("processes", [])
        if output.get("node_id") != CHILD or len(expected) != 2:
            raise RuntimeError("owned_process_identity_mismatch")
        if not all(x.get("starttime_matches") and x.get("uid_matches") and x.get("exe_sha_matches") and x.get("argv_owner_path_matches") for x in expected):
            raise RuntimeError("owned_process_fact_mismatch")
        if not output.get("proxy", {}).get("listening") or not output.get("proxy", {}).get("owned_by_supervisor") or not output.get("controls_empty"):
            raise RuntimeError("owned_proxy_or_controls_mismatch")
        api.write_json(HERE / "owned-process-ledger.json", {
            "status": "PASS",
            "node_id": CHILD,
            "agent_id": output.get("agent_id"),
            "proxy_port": PROXY_PORT,
            "owner_manifest_sha256": output.get("manifest_sha256"),
            "child": expected[0],
            "supervisor": expected[1],
            "proxy": output.get("proxy"),
            "controls_empty": output.get("controls_empty"),
            "task_id": result.get("task_id"),
            "execution_id": result.get("execution_id"),
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        })
    finally:
        if task_id:
            status, _ = api.request(TOKEN, "DELETE", "/api/tasks/" + task_id)
            get_status = api.route_status(TOKEN, "/api/tasks/" + task_id)
            api.write_json(HERE / "owned-process-probe-cleanup.json", {"task_id": task_id, "delete_http": status, "get_after_delete_http": get_status})
            if status != 200 or get_status != 404:
                raise RuntimeError("owned_process_probe_task_cleanup_failed")
    print(json.dumps({"status": "PASS", "task_id": result.get("task_id"), "execution_id": result.get("execution_id"), "node_id": CHILD, "proxy_port": PROXY_PORT, "ledger": str(HERE / "owned-process-ledger.json")}))


if __name__ == "__main__":
    main()
