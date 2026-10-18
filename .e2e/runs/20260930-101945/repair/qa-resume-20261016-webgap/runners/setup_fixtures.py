#!/usr/bin/env python3
"""创建 owned fixture：脚本（长命令/12 个长参数）+ 任务（长中文名）on native-2，并 run now。

产出：evidence/fixtures-task.json（含 id 供清理与后续用例复用）
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from api import j, login  # noqa: E402

PFX = "qa-webgap-6456ada2"
NATIVE2 = "611c02a5-1859-4465-878b-d543b7f6fdec"
NATIVE4 = "03cb50cc-eab6-4afd-bbe0-f73c4e326773"
HOST001 = "f6a79d23-2410-450c-ac39-64dd70c9daa1"
BASE_DIR = "/var/lib/" + PFX
SRC = BASE_DIR + "/深中文目录名称用于长值布局验证/very-long-file-name-abcdefghijklmnopqrstuvwxyz-0123456789-abcdefghijklmnopqrstuvwxyz.bin"
DST_OK = BASE_DIR + "/深中文目录名称用于长值布局验证/目标副本-copy-of-the-very-long-file-name-abcdefghijklmnopqrstuvwxyz-0123456789.bin"

tok = login()
OUT = {"prefix": PFX, "node_scope": "native-2 (192.168.100.206) only",
       "src": SRC, "dst_ok": DST_OK, "ids": {}}

SCRIPT = """#!/bin/sh
set -u
BASE="__BASE_DIR__"
SRC="__SRC__"
mkdir -p "$(dirname "$SRC")"
head -c 1048576 /dev/urandom > "$SRC"
chmod 644 "$SRC"
echo "SRC=$SRC"
ls -l "$SRC"
sha256sum "$SRC"
i=1
while [ $i -le 3000 ]; do
  printf 'qa-webgap-6456ada2 超长日志行 %05d 参数01=%s 参数02=%s 填充=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\\n' "$i" "$qa_webgap_long_parameter_name_01" "$qa_webgap_long_parameter_name_02"
  i=$((i+1))
done
echo "log-line-count=3000"
sleep 70
echo "qa-webgap-6456ada2 done"
""".replace("__BASE_DIR__", BASE_DIR).replace("__SRC__", SRC)

params = []
for i in range(1, 13):
    params.append({
        "name": "qa_webgap_long_parameter_name_%02d" % i,
        "type": "string",
        "required": False,
        "default": ("默认值-超长参数默认内容-0123456789-abcdefghijklmnopqrstuvwxyz-%02d" % i),
        "description": "长参数描述：用于长值布局验证的第 %02d 个参数，内容刻意拉长。" % i,
    })

script = {
    "name": PFX + "-脚本超长名称用于长值布局验证-中文标题-0123456789-abcdefghijklmnopqrstuvwxyz",
    "description": "长值布局验证脚本：创建长路径源文件、输出 3000 行长日志、12 个长参数。",
    "interpreter": "shell",
    "content": SCRIPT,
    "parameters": params,
    "environment": {"QA_WEBGAP_ENV": "qa-webgap-6456ada2-env-value-with-a-very-long-content-0123456789"},
    "working_dir": BASE_DIR,
    "timeout": 300,
}
st, sc = j("POST", "/api/scripts", tok, script)
OUT["script_create"] = {"http": st, "id": sc.get("id") if isinstance(sc, dict) else sc}
print("script", st, OUT["script_create"])
tok = login()
sid = sc["id"]
OUT["ids"]["script_id"] = sid

param_values = {
    "qa_webgap_long_parameter_name_01": "参数值-非常长的中文与字母数字混排内容-0123456789-abcdefghijklmnopqrstuvwxyz-超长结束",
    "qa_webgap_long_parameter_name_02": "第二个参数值也很长-0123456789-abcdefghijklmnopqrstuvwxyz",
}
task = {
    "name": PFX + "-任务长名称用于长值布局验证-中文超长标题-0123456789-abcdefghijklmnopqrstuvwxyz-结束",
    "type": "script",
    "script_id": sid,
    "target": {"type": "node", "node_ids": [NATIVE2]},
    "parameters": params,
    "param_values": param_values,
    "timeout": 300,
    "retry": 0,
    "offline_policy": "allow_offline",
    "enabled": True,
}
st, tk = j("POST", "/api/tasks", tok, task)
print("task", st, json.dumps(tk, ensure_ascii=False)[:400])
OUT["task_create"] = {"http": st}
OUT["ids"]["task_id"] = tk["id"]

# run now
st, run = j("POST", "/api/tasks/%s/run" % tk["id"], tok, {"params": param_values})
OUT["run_now"] = {"http": st, "resp": run if st != 200 else "ok"}
print("run", st, json.dumps(run, ensure_ascii=False)[:300] if st != 200 else "ok")

# schedule（cron 每年 1/1 03:00，测试窗口内不触发）
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
OUT["schedule_create"] = {"http": st, "id": sd.get("id") if isinstance(sd, dict) else sd}
print("schedule", st, OUT["schedule_create"])
if st == 200:
    OUT["ids"]["schedule_id"] = sd["id"]

OUT["created_at_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
p = Path(__file__).resolve().parent.parent / "evidence" / "fixtures-setup.json"
p.write_text(json.dumps(OUT, ensure_ascii=False, indent=1) + "\n")
print("WROTE", p)
