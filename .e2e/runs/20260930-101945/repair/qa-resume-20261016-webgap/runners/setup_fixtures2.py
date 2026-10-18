#!/usr/bin/env python3
"""修复：把已创建脚本 enabled=true，再创建任务（长中文名）+ run now + schedule。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api import j, login  # noqa: E402

PFX = "qa-webgap-6456ada2"
NATIVE2 = "611c02a5-1859-4465-878b-d543b7f6fdec"
BASE_DIR = "/var/lib/" + PFX
SRC = BASE_DIR + "/深中文目录名称用于长值布局验证/very-long-file-name-abcdefghijklmnopqrstuvwxyz-0123456789-abcdefghijklmnopqrstuvwxyz.bin"
DST_OK = BASE_DIR + "/深中文目录名称用于长值布局验证/目标副本-copy-of-the-very-long-file-name-abcdefghijklmnopqrstuvwxyz-0123456789.bin"
SID = "29822361-f6a0-425d-aa48-6316cf059dd1"

OUT = {"prefix": PFX, "node_scope": "native-2 (192.168.100.206) only", "src": SRC, "dst_ok": DST_OK, "ids": {}}
tok = login()

st, sc = j("GET", "/api/scripts/%s" % SID, tok)
sc["enabled"] = True
st, upd = j("PUT", "/api/scripts/%s" % SID, tok, sc)
print("script enable", st, (upd.get("enabled") if isinstance(upd, dict) else upd))
tok = login()
OUT["ids"]["script_id"] = SID
OUT["script_enabled"] = sc.get("enabled")
params = sc["parameters"]
param_values = {
    "qa_webgap_long_parameter_name_01": "参数值-非常长的中文与字母数字混排内容-0123456789-abcdefghijklmnopqrstuvwxyz-超长结束",
    "qa_webgap_long_parameter_name_02": "第二个参数值也很长-0123456789-abcdefghijklmnopqrstuvwxyz",
}
task = {
    "name": PFX + "-任务长名称用于长值布局验证-中文超长标题-0123456789-abcdefghijklmnopqrstuvwxyz-结束",
    "type": "script",
    "script_id": SID,
    "target": {"type": "node", "node_ids": [NATIVE2]},
    "parameters": params,
    "param_values": param_values,
    "timeout": 300,
    "retry": 0,
    "offline_policy": "allow_offline",
    "enabled": True,
}
st, tk = j("POST", "/api/tasks", tok, task)
print("task", st, json.dumps(tk, ensure_ascii=False)[:300])
OUT["ids"]["task_id"] = tk["id"]

st, run = j("POST", "/api/tasks/%s/run" % tk["id"], tok, {"params": param_values})
OUT["run_now"] = {"http": st, "resp": run if st != 200 else "ok"}
print("run", st)

sched = {
    "task_id": tk["id"],
    "type": "cron",
    "expression": "0 3 1 1 *",
    "timezone": "Asia/Shanghai",
    "execution_owner": "hub",
    "offline_policy": "allow_offline",
    "misfire_policy": "skip",
    "enabled": True,
}
st, sd = j("POST", "/api/schedules", tok, sched)
print("schedule", st, json.dumps(sd, ensure_ascii=False)[:200] if st != 200 else sd.get("id"))
if st == 200:
    OUT["ids"]["schedule_id"] = sd["id"]
OUT["schedule_http"] = st

OUT["created_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
p = Path(__file__).resolve().parent.parent / "evidence" / "fixtures-setup.json"
p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
print("WROTE", p)
