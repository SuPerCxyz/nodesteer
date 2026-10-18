"""P-TABLE：在隔离实例内创建 owned fixtures（可随实例销毁）。

统一通过浏览器内 authenticated fetch 调用真实 API；非 UI 造数的部分在 fixture-ledger 中如实登记。
"""
from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ptable_common as C  # noqa: E402

FIX_NAMES = "fixtures.json"


def build_js(nonce: str, user_pw: str) -> str:
    zh_long = "长中文名称用于边界验证" * 6  # 72 个汉字
    long_cmd = "echo " + ("A" * 2139)      # 2144 字符命令，用于 G04-07/X20 长值边界
    return r"""
const nonce = __NONCE__;
const zhLong = __ZHLONG__;
const longCmd = __LONGCMD__;
const userPw = __USERPW__;
const t = localStorage.getItem('nodesteer_token');
const H = { Authorization: 'Bearer ' + t, 'Content-Type': 'application/json' };
const out = { created: {}, errors: [], ids: {} };
const j = async (method, path, body) => {
  const r = await fetch(path, { method, headers: H, body: body === undefined ? undefined : JSON.stringify(body) });
  let b = null; try { b = await r.json(); } catch (e) {}
  if (r.status >= 300) out.errors.push({ path, method, status: r.status, body: b });
  return { status: r.status, body: b };
};
const g = async (path) => { const r = await fetch(path, { headers: H }); return { status: r.status, body: await r.json().catch(() => null) }; };

// 1) nodes：通过真实 enrollment 端点准备节点（无 Agent 连接，状态为 pending）
const nodeIds = [];
let nodes = (await g('/api/nodes')).body || [];
if (!Array.isArray(nodes)) nodes = [];
nodeIds.push(...nodes.map(x => x.id));
if (nodes.length < 12) {
  for (let i = nodes.length; i < 12; i++) {
    const nm = `ptable-${nonce}-n${String(i + 1).padStart(2, '0')}`;
    const r = await j('POST', '/api/nodes/enrollment', {
      node_name: nm, node_ip: `10.42.0.${11 + i}`, hub_address: 'http://127.0.0.1:18082',
    });
    if (r.body && r.body.node_id) nodeIds.push(r.body.node_id);
  }
}
out.ids.node_ids = nodeIds.slice(0, 14);
// 长名称节点（≤128、仅字母数字-_。），用于 X20 长值边界
if (!nodeIds.some((id, i) => (nodes[i]?.hostname || '').length > 60)) {
  const r = await j('POST', '/api/nodes/enrollment', { node_name: 'ptable-' + nonce + '-' + 'L'.repeat(70), node_ip: '10.42.9.9', hub_address: null });
  if (r.body && r.body.node_id) nodeIds.push(r.body.node_id);
}
out.ids.long_node_id = nodeIds[nodeIds.length - 1];
out.created.nodes = ((await g('/api/nodes')).body || []).length;
const nodeA = nodeIds[0], nodeB = nodeIds[1];

// 2) groups
const groups = (await g('/api/groups')).body || [];
if (groups.length < 12) for (let i = groups.length; i < 12; i++) {
  await j('POST', '/api/groups', { name: `ptable-${nonce}-g${String(i + 1).padStart(2, '0')}`, description: 'p-table fixture', type: 'static', members: [] });
}
await j('POST', '/api/groups', { name: `ptable-${nonce}-${zhLong}`, description: '长中文分组名', type: 'static', members: [] });
out.created.groups = ((await g('/api/groups')).body || []).length;

// 3) scripts（含长中文名与长内容）
const scripts = (await g('/api/scripts')).body || [];
if (scripts.length < 12) for (let i = scripts.length; i < 12; i++) {
  await j('POST', '/api/scripts', { name: `ptable-${nonce}-s${String(i + 1).padStart(2, '0')}`, description: 'p-table fixture', interpreter: 'bash', content: `echo fixture ${i + 1}`, parameters: [], environment: {}, working_dir: '/tmp', timeout: 30, enabled: true });
}
const longScript = await j('POST', '/api/scripts', { name: `ptable-${nonce}-${zhLong}`, description: '长中文脚本名 + 长命令内容', interpreter: 'bash', content: longCmd, parameters: [], environment: {}, working_dir: '/tmp', timeout: 30, enabled: true });
out.ids.long_script_id = longScript.body && longScript.body.id;
out.created.scripts = ((await g('/api/scripts')).body || []).length;

// 4) applications
const apps = (await g('/api/applications')).body || [];
if (apps.length < 12) for (let i = apps.length; i < 12; i++) {
  await j('POST', '/api/applications', { name: `ptable-${nonce}-a${String(i + 1).padStart(2, '0')}`, description: 'p-table fixture', artifact_id: '', version: '1.0.0', binary_path: '/usr/local/bin/ptable', arguments: [], environment: {}, config: '', config_path: '/etc/ptable.conf', unit_name: `nodesteer-ptable-${i + 1}.service` });
}
const longApp = await j('POST', '/api/applications', { name: `ptable-${nonce}-${zhLong}`, description: '长中文应用名', artifact_id: '', version: '1.0.0', binary_path: '/usr/local/bin/' + 'p'.repeat(120), arguments: [], environment: {}, config: '', config_path: '/' + 'd'.repeat(120) + '/app.conf', unit_name: `nodesteer-ptable-long.service` });
out.ids.long_app_id = longApp.body && longApp.body.id;
out.created.applications = ((await g('/api/applications')).body || []).length;

// 5) artifacts（multipart）
const arts = (await g('/api/artifacts')).body || [];
if (arts.length < 12) for (let i = arts.length; i < 12; i++) {
  const fd = new FormData();
  fd.append('name', `ptable-${nonce}-art${String(i + 1).padStart(2, '0')}`);
  fd.append('version', `1.0.${i + 1}`);
  fd.append('architecture', 'amd64');
  fd.append('file', new File([`p-table fixture ${i + 1}`], `ptable-fixture-${i + 1}.txt`, { type: 'text/plain' }));
  const r = await fetch('/api/artifacts', { method: 'POST', headers: { Authorization: 'Bearer ' + t }, body: fd });
  if (r.status >= 300) out.errors.push({ path: '/api/artifacts', method: 'POST', status: r.status });
}
const fdL = new FormData();
fdL.append('name', `ptable-${nonce}-${zhLong}`);
fdL.append('version', '9.9.9-long');
fdL.append('architecture', 'amd64');
fdL.append('file', new File(['long'], 'ptable-' + 'f'.repeat(80) + '.txt', { type: 'text/plain' }));
await fetch('/api/artifacts', { method: 'POST', headers: { Authorization: 'Bearer ' + t }, body: fdL });
out.created.artifacts = ((await g('/api/artifacts')).body || []).length;

// 6) users
const users = (await g('/api/users')).body || [];
if (users.length < 12) for (let i = users.length; i < 12; i++) {
  await j('POST', '/api/users', { username: `ptable-${nonce}-u${String(i + 1).padStart(2, '0')}`, password: userPw, role: 'viewer' });
}
out.created.users = ((await g('/api/users')).body || []).length;

// 7) tasks（command 类型，target 指向已准备节点）
const tasks = (await g('/api/tasks')).body || [];
if (tasks.length < 12) for (let i = tasks.length; i < 12; i++) {
  const r = await j('POST', '/api/tasks', { name: `ptable-${nonce}-t${String(i + 1).padStart(2, '0')}`, type: 'command', command: `echo ptable-${i + 1}`, target: { type: 'node', node_ids: [nodeA] }, parameters: [], param_values: {}, timeout: 60, retry: 0, offline_policy: 'allow_offline', enabled: true });
  if (r.body && r.body.id) tasks.push(r.body);
}
const longTask = await j('POST', '/api/tasks', { name: `ptable-${nonce}-${zhLong}`, type: 'command', command: longCmd, target: { type: 'node', node_ids: [nodeA] }, parameters: [], param_values: {}, timeout: 60, retry: 0, offline_policy: 'allow_offline', enabled: true });
out.ids.long_task_id = longTask.body && longTask.body.id;
out.created.tasks = ((await g('/api/tasks')).body || []).length;

// 8) schedules（引用第一个 task）
const scheds = (await g('/api/schedules')).body || [];
if (scheds.length < 12) for (let i = scheds.length; i < 12; i++) {
  await j('POST', '/api/schedules', { task_id: tasks[0].id, type: 'interval', interval_sec: 3600 + i, execution_owner: 'hub', timezone: 'UTC', offline_policy: 'allow_offline', misfire_policy: 'run_once', enabled: true });
}
out.created.schedules = ((await g('/api/schedules')).body || []).length;

// 9) transfers（源/目标均为本实例已准备节点；无 Agent 在线时保持 pending）
const trs = (await g('/api/transfers')).body || [];
if (trs.length < 12) for (let i = trs.length; i < 12; i++) {
  await j('POST', '/api/transfers', { source_node_id: nodeA, source_path: `/var/tmp/ptable-${nonce}-${i + 1}.txt`, targets: [{ node_id: nodeB, destination_path: `/var/tmp/ptable-dst-${i + 1}.txt`, mode: 'copy' }] });
}
const trs2 = (await g('/api/transfers')).body || [];
out.created.transfers = trs2.length;
out.ids.transfer_ids = trs2.slice(0, 3).map(x => x.id);
// 审计行由上述真实写操作自动产生
out.created.audit = ((await g('/api/audit?limit=1&offset=0')).body?.total) ?? null;
out.final_counts = {
  nodes: ((await g('/api/nodes')).body || []).length,
  groups: ((await g('/api/groups')).body || []).length,
  scripts: ((await g('/api/scripts')).body || []).length,
  applications: ((await g('/api/applications')).body || []).length,
  artifacts: ((await g('/api/artifacts')).body || []).length,
  users: ((await g('/api/users')).body || []).length,
  tasks: ((await g('/api/tasks')).body || []).length,
  schedules: ((await g('/api/schedules')).body || []).length,
  transfers: ((await g('/api/transfers')).body || []).length,
  audit_total: ((await g('/api/audit?limit=1&offset=0')).body || {}).total,
  executions: ((await g('/api/executions?limit=1&offset=0')).body || {}).total,
};
return out;
""".replace("__NONCE__", json.dumps(nonce)).replace(
        "__ZHLONG__", json.dumps(zh_long)).replace(
        "__LONGCMD__", json.dumps(long_cmd)).replace(
        "__USERPW__", json.dumps(user_pw))


def main() -> None:
    led = json.loads((C.ROOT / "instance-ledger.json").read_text())
    nonce = led["nonce"]
    creds = C.load_creds(nonce)
    if "fixture_password" not in creds:
        creds["fixture_password"] = C.gen_password()
        C.save_creds(nonce, creds)
    C.ab("open", C.origin() + "/nodes", timeout=45)
    C.ab("wait", "--load", "networkidle", timeout=30)
    res = C.ab_json(build_js(nonce, creds["fixture_password"]), timeout=300)
    ledger = {
        "instance_id": led["instance_id"],
        "captured_at_utc": C.utc(),
        "creation_path": "浏览器内 authenticated fetch 调用真实 Hub API（enrollment/groups/scripts/applications/artifacts(multipart)/users/tasks/schedules/transfers）",
        "ownership": f"全部名称含 nonce {nonce}；对象仅存在于隔离实例 data_dir，随实例销毁",
        "created_counts": res.get("created"),
        "final_counts": res.get("final_counts"),
        "errors": res.get("errors", []),
        "long_value_fixtures": {k: v for k, v in (res.get("ids") or {}).items()
                                if k.startswith("long")},
        "notes": [
            "nodes 通过真实 POST /api/nodes/enrollment 准备，无 Agent 连接，状态为 pending（非伪造行）。",
            "artifacts 通过 multipart 真实上传。",
            "transfers 通过真实 POST /api/transfers 创建，源/目标为本实例 pending 节点；无 Agent 在线故保持 pending。",
            "executions 无法在不运行真实 Agent 的前提下产生（产品语义：执行由 Agent 上报）。",
        ],
    }
    (C.ROOT / "fixture-ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"final_counts": ledger["final_counts"],
                      "errors": len(ledger["errors"]),
                      "created": ledger["created_counts"]},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
