#!/usr/bin/env python3
"""OBS-20261014-AR26-ROLLBACK-UNCLOSED 判定探针（新构建 dddee4f0-e2e-repair-20261017-fixall）。

场景：在 owned child 上做**原生应用首次安装**（宿主原本无该 unit/binary/config），
安装后健康检查必然失败（tcp 指向关闭端口）→ 检查：
 1. Agent 部署 journal 是否停在 ROLLING_BACK（未闭合）；
 2. 证据原文是否仍有 "rollback failed"；
 3. 宿主是否残留半安装（unit/binary/config）。
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hubclient  # noqa: E402
import hostlib  # noqa: E402

EV = HERE.parent
OUT = EV / "ar26-first-install-rollback.json"
h = hubclient.Hub()
h.login()
NONCE = secrets.token_hex(4)
P = "qa-tail-%s-" % NONCE
UNIT = P + "firstinstall.service"
BINARY = "/opt/%sbin/%sapp" % (P, P)
CONFIG = "/etc/%sfirstinstall.conf" % P
child = None
created = []


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def wait_exec(ex_id, timeout=240):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        code, ex = h.raw("GET", "/api/executions/" + ex_id)
        if isinstance(ex, dict):
            last = ex
            if ex.get("status") in ("SUCCESS", "FAILED", "SKIPPED", "BLOCKED", "CANCELED", "TIMED_OUT"):
                return ex
        time.sleep(2)
    return last


def main():
    res = {"nonce": NONCE, "at_utc": now(), "unit": UNIT, "binary": BINARY, "config": CONFIG,
           "steps": []}

    # 1) owned node
    code, enr = h.raw("POST", "/api/nodes/enrollment",
                      {"node_name": P + "child", "node_ip": "10.97.0.9"})
    nid = enr.get("node_id") or enr.get("id")
    res["steps"].append({"step": "enroll owned node", "http": code, "node_id": nid})
    code, nodes = h.raw("GET", "/api/nodes")
    node = next((n for n in (nodes if isinstance(nodes, list) else []) if n.get("id") == nid), {})
    aid = node.get("agent_id")
    res["steps"].append({"step": "read agent_id", "agent_id": aid, "status": node.get("status")})
    created.append(("node", nid))

    # 2) start owned child agent on native-1
    cred = json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-credentials.json').read_text())
    child = hostlib.OwnedChild(NONCE, "n1", tag="i3", node_id=nid, agent_id=aid,
                               node_name=P + "child", node_ip="10.97.0.9",
                               token=cred["registration_token"])
    hurl = "ws://192.168.100.209:18080"
    res["steps"].append({"step": "child paths", "root": child.root, "config": child.config})
    try:
        info = child.write_config_and_start(hurl, extra_yaml="")
        res["steps"].append({"step": "start owned child agent", "info": info})
    except Exception as e:  # noqa: BLE001
        res["steps"].append({"step": "start owned child agent FAILED", "error": repr(e)[:400]})
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
        print(json.dumps(res, ensure_ascii=False, indent=1)); return

    # wait for online
    online = None
    for _ in range(30):
        code, n = h.raw("GET", "/api/nodes/" + nid)
        if isinstance(n, dict) and n.get("status") == "online":
            online = n
            break
        time.sleep(2)
    res["steps"].append({"step": "wait node online", "status": (online or {}).get("status"),
                         "agent_version": (online or {}).get("agent_version"),
                         "capabilities": (online or {}).get("capabilities")})
    if not online:
        res["aborted"] = "node never came online"
        OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
        print(json.dumps(res, ensure_ascii=False, indent=1)); return

    # 3) artifact（首次安装用二进制）
    content = b"#!/bin/sh\nexec sleep 3600\n"
    code, art = h.upload_artifact(P + "art", "1.0", "payload.sh", content)
    art_id = art.get("id") if isinstance(art, dict) else None
    res["steps"].append({"step": "upload artifact", "http": code, "id": art_id})
    created.append(("artifact", art_id))

    # 4) closed port on the child host → health check must fail
    closed = child.remote_py(
        "import json,socket\n"
        "s=socket.socket();s.bind(('127.0.0.1',0));p=s.getsockname()[1];s.close()\n"
        "print(json.dumps({'port':p}))\n")["port"]
    res["closed_port"] = closed

    # 5) owned application: FIRST INSTALL（宿主无 unit/binary/config）
    pre = child.remote_py(
        "import json,os\n"
        "print(json.dumps({'unit':os.path.lexists(%(u)r),'binary':os.path.lexists(%(b)r),"
        "'config':os.path.lexists(%(c)r)}))\n"
        % {"u": "/etc/systemd/system/" + UNIT, "b": BINARY, "c": CONFIG})
    res["host_before_install"] = pre

    payload = {"name": P + "firstinstall", "description": "AR26 first-install probe",
               "artifact_id": art_id, "version": "1.0",
               "binary_path": BINARY, "arguments": [], "environment": {},
               "config": "QA_TAIL_MARK=%s\n" % NONCE, "config_path": CONFIG,
               "unit_name": UNIT,
               "health_check": {"type": "tcp", "target": "127.0.0.1:%d" % closed,
                                "timeout": 2, "attempts": 2, "interval": 1}}
    code, app = h.raw("POST", "/api/applications", payload)
    app_id = app.get("id") if isinstance(app, dict) else None
    res["steps"].append({"step": "create application (no prior install on host)", "http": code,
                         "app_id": app_id, "version": app.get("version") if isinstance(app, dict) else None})
    created.append(("application", app_id))

    code, r = h.raw("POST", "/api/applications/%s/assign" % app_id, {"node_ids": [nid]})
    res["steps"].append({"step": "assign to owned child", "http": code})

    t0 = time.time()
    code, execs = h.raw("POST", "/api/applications/%s/deploy" % app_id,
                        {"node_ids": [nid], "operation": "deploy"})
    res["steps"].append({"step": "deploy (first install)", "http": code})
    ex = wait_exec(execs[0]["id"]) if code == 200 and execs else None
    res["steps"].append({"step": "wait execution", "elapsed_s": round(time.time() - t0, 1),
                         "execution": {k: (ex or {}).get(k) for k in
                                       ("id", "status", "block_reason", "error", "offline")}})
    res["failure_text"] = " ".join(str((ex or {}).get(k) or "") for k in ("block_reason", "error")).strip()

    time.sleep(4)
    # 6) journal + host readback
    probe = child.remote_py(
        "import json,os,sqlite3,subprocess,hashlib\n"
        "unit=%(unit)r; data=%(data)r; bp=%(bp)r; cp=%(cp)r\n"
        "out={'at_utc':%(now)r}\n"
        "up='/etc/systemd/system/'+unit\n"
        "out['unit_file_exists']=os.path.lexists(up)\n"
        "out['unit_enable_symlink']=os.path.lexists('/etc/systemd/system/multi-user.target.wants/'+unit)\n"
        "out['binary_exists']=os.path.lexists(bp)\n"
        "out['binary_dir_exists']=os.path.isdir(os.path.dirname(bp))\n"
        "out['config_exists']=os.path.lexists(cp)\n"
        "r=subprocess.run(['systemctl','show','--no-pager','--property=Id,LoadState,ActiveState,SubState,UnitFileState',unit],capture_output=True,text=True)\n"
        "out['unit_state']=dict(x.split('=',1) for x in r.stdout.splitlines() if '=' in x)\n"
        "c=sqlite3.connect('file:'+os.path.join(data,'state.db')+'?mode=ro',uri=True)\n"
        "c.execute('PRAGMA query_only=ON')\n"
        "out['deployments']=[{'id':r0[0],'to_version':r0[1],'phase':r0[2],'completed_kind':r0[3],'updated_at':r0[4]} for r0 in c.execute('SELECT id,to_version,phase,completed_kind,updated_at FROM deployments ORDER BY started_at DESC LIMIT 8')]\n"
        "try:\n"
        "    out['deployments_columns']=[d[1] for d in c.execute('PRAGMA table_info(deployments)')]\n"
        "except Exception as e: out['cols_err']=str(e)\n"
        "print(json.dumps(out))\n"
        % {"unit": UNIT, "data": child.data, "bp": BINARY, "cp": CONFIG, "now": now()})
    res["host_after_failure"] = probe

    deps = probe.get("deployments") or []
    phases = [d.get("phase") for d in deps]
    res["verdict"] = {
        "journal_closed": all(p != "ROLLING_BACK" for p in phases),
        "phases": phases,
        "completed_kinds": [d.get("completed_kind") for d in deps],
        "failure_text_no_rollback_failed": "rollback failed" not in res["failure_text"],
        "no_half_install": (not probe.get("unit_file_exists") and not probe.get("binary_exists")
                            and not probe.get("config_exists")),
        "first_install_was_real": (pre.get("unit") is False and pre.get("binary") is False
                                   and pre.get("config") is False),
    }

    # 7) cleanup
    code, _ = h.raw("DELETE", "/api/applications/" + app_id)
    res["steps"].append({"step": "cleanup application", "http": code})
    code, _ = h.raw("GET", "/api/applications/" + app_id)
    res["steps"].append({"step": "verify app 404", "http": code})

    # host residue removal（owned paths only）
    cleanup = child.remote_py(
        "import json,os,subprocess,shutil\n"
        "unit=%(unit)r; bp=%(bp)r; cp=%(cp)r; root=%(root)r\n"
        "subprocess.run(['systemctl','stop',unit],capture_output=True)\n"
        "subprocess.run(['systemctl','disable',unit],capture_output=True)\n"
        "for p in ['/etc/systemd/system/'+unit,'/etc/systemd/system/multi-user.target.wants/'+unit,bp,cp]:\n"
        "    try: os.remove(p)\n"
        "    except FileNotFoundError: pass\n"
        "shutil.rmtree(os.path.dirname(bp), ignore_errors=True)\n"
        "subprocess.run(['systemctl','daemon-reload'],capture_output=True)\n"
        "print(json.dumps({'unit':os.path.lexists('/etc/systemd/system/'+unit),'binary':os.path.lexists(bp),'config':os.path.lexists(cp),'binary_dir':os.path.isdir(os.path.dirname(bp))}))\n"
        % {"unit": UNIT, "bp": BINARY, "cp": CONFIG, "root": child.root})
    res["host_cleanup"] = cleanup

    stop = child.stop()
    res["child_stop"] = stop
    code, _ = h.raw("DELETE", "/api/nodes/" + nid)
    code2, _ = h.raw("GET", "/api/nodes/" + nid)
    res["steps"].append({"step": "cleanup node", "delete": code, "after_get": code2})
    if art_id:
        code, _ = h.raw("DELETE", "/api/artifacts/" + art_id)
        code2, _ = h.raw("GET", "/api/artifacts/" + art_id)
        res["steps"].append({"step": "cleanup artifact", "delete": code, "after_get": code2})

    rm = child.sh("rm -rf %s" % child.root)
    res["child_root_removed"] = {"rc": rm[0]}
    rm2 = child.sh("ls -d %s 2>/dev/null | wc -l" % child.root)
    res["child_root_left"] = rm2[1].strip()

    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "steps"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    try:
        main()
    finally:
        pass
