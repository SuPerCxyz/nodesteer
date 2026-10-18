#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate control-coverage-audit.md from control-inventory.json."""
import json, os
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(BASE)
inv = json.load(open(f'{OUT}/control-inventory.json', encoding='utf-8'))

L = []
w = L.append

w('# NodeSteer repair 环境 · 控件级覆盖审计（只读）')
w('')
w(f'- 生成时间：{inv["generated_at"]}')
w(f'- 构建：`{inv["build_label"]}`；UI：{inv["ui"]}')
w(f'- 用例基线：`{inv["cases_source"]}`（{inv["cases_total"]} 条用例）')
w('- 审计方式：agent-browser 真实浏览器遍历（自有 session `ctrlaudit-20261017`），'
  '仅做只读打开 / 展开菜单对话框 / Esc 关闭；**未点击任何删除、提交、运行、启停、取消传输等写操作控件**。')
w('')
w('## 0. 结论摘要')
w('')
verdicts = {}
for p in inv['pages']:
    for c in p['controls']:
        verdicts[c['coverage_verdict']] = verdicts.get(c['coverage_verdict'], 0) + 1
w('| 指标 | 数值 |')
w('|---|---|')
w(f'| 可达页面 / 页面态 | {inv["pages_total"]} |')
w(f'| 控件总数（按页面 + 可访问名去重） | {inv["controls_total"]} |')
w(f'| 已覆盖（能对应用例且用例 PASS） | {verdicts.get("已覆盖", 0)} |')
w(f'| 部分覆盖（仅能对上 PARTIAL/FAIL 用例，或效果未被专门验证） | {verdicts.get("部分覆盖", 0)} |')
w(f'| 未覆盖（找不到对应用例） | {verdicts.get("未覆盖", 0)} |')
w(f'| 跨页联动检查项 | {len(inv["cross_page_linkage"])}（确认缺失 '
  f'{sum(1 for x in inv["cross_page_linkage"] if x.get("gap") and x["gap"].startswith("确认缺失"))}，'
  f'需夹具待验 {sum(1 for x in inv["cross_page_linkage"] if x.get("gap") and x["gap"].startswith("需夹具"))}） |')
w(f'| 需夹具页面/区块 | {len(inv["fixture_required"])} |')
w('')
w('> 说明：本环境为共享 repair 权威（:18080），审计期间另一条 QA 链（`qa-p01-*`）在并发创建/清理任务，'
  '因此“任务列表/任务详情”等页面在捕获窗口内存在数据漂移；本报告按捕获时刻的实际状态记录，并对不可复现项标注“需夹具”。')
w('')
w('## 1. 审计方法与判据')
w('')
w('- **遍历**：侧栏 11 个导航项 + 品牌链接 + 顶栏语言/主题/资料菜单 + 全部列表/新建/详情/深链路由；'
  '行内操作菜单、视图（列可见性）菜单、添加节点对话框、新建用户对话框、角色下拉、批量工具条、'
  '节点详情标签表单、执行日志区、404 与 7 类“不存在资源”错误态。')
w('- **控件采集**：`agent-browser eval` 提取 `a/button/input/select/textarea/[role=*]` 等可交互元素，'
  '取可访问名（aria-label → aria-labelledby → label → placeholder → 邻近 label），按页面 + 可访问名去重；'
  '同时保存 a11y 快照与截图。')
w('- **覆盖判据**：在 504 用例的 `title` / `actual` / `covered_dimensions.description` 中能否找到该控件对应的用例；'
  '能对上且存在 PASS → 已覆盖；只能对上 PARTIAL/FAIL，或该控件的行为未被专门验证 → 部分覆盖；找不到 → 未覆盖。')
w('- **档位**：必须补测（可只读复测）/ 需夹具（需先准备数据）/ 需授权（需写操作授权）。')
w('')
w('## 2. 逐页覆盖表')
w('')
w('| 页面 | 路由 | 状态 | 控件 | 已覆盖 | 部分 | 未覆盖 | 备注 |')
w('|---|---|---|---|---|---|---|---|')
for p in inv['pages']:
    v = {}
    for c in p['controls']:
        v[c['coverage_verdict']] = v.get(c['coverage_verdict'], 0) + 1
    w(f'| {p["title"]} | `{p["route"]}` | {p["state"]} | {p["controls_total"]} | '
      f'{v.get("已覆盖", 0)} | {v.get("部分覆盖", 0)} | {v.get("未覆盖", 0)} | {p["note"]} |')
w('')
w('## 3. 未覆盖 / 部分覆盖清单（分档）')
w('')
w('### 3.0 待补测 Top 10（按风险与影响排序）')
w('')
w('| # | 控件/区块 | 页面 | 档位 | 依据 |')
w('|---|---|---|---|---|')
TOP10 = [
    ('选择该行 / 全选所有行', '节点列表 /agents', '必须补测', 'X14 FAIL（WEB-NODE-SELECTION-DRIFT-20261017 选择漂移）'),
    ('清除选择（批量工具条）', '节点列表 /agents', '必须补测', 'X19 PARTIAL（批量工具条键盘/清除）'),
    ('触发方式 过滤', '执行列表 /executions', '必须补测', 'P17P18-04 PARTIAL（trigger 过滤）'),
    ('时区', '新建调度 /schedules/new', '必须补测', 'P12P13-05、X10 均 PARTIAL'),
    ('Skip to Main', '全局壳', '必须补测', 'G04-04、X19 均 PARTIAL（键盘可达性）'),
    ('打开日志', '执行详情 /executions/{id}', '必须补测', '实为 scrollIntoView，行为无专门用例'),
    ('取消', '编辑脚本 / 编辑应用 / 上传发布包', '必须补测', 'X08、X06 PARTIAL（取消零写请求）'),
    ('上传新发布包（应用内嵌）', '编辑托管应用 /applications/{id}', '必须补测', 'X06 PARTIAL'),
    ('编辑任务 /tasks/{id}/edit', '任务编辑页', '需夹具', '任务被并发清理，页面为错误态'),
    ('立即运行 /tasks/{id}/run', '任务运行页', '需夹具', '任务被并发清理，页面为错误态'),
]
for i, (name, page, tier, why) in enumerate(TOP10, 1):
    w(f'| {i} | {name} | {page} | {tier} | {why} |')
w('')
w('> 其余待补：`/groups/{id}`、`/schedules/{id}`（需夹具）；审计→资源详情、传输/用户/发布包详情页（无控件或跨页联动缺口）。')
w('')
w('### 3.1 必须补测（可只读复测）')
w('')
w('| 控件 | 页面 | 分类 | 现有用例 | 用例状态 | 说明 |')
w('|---|---|---|---|---|---|')
for p in inv['pages']:
    for c in p['controls']:
        if c['coverage_verdict'] == '部分覆盖' and c['gap_tier'] == '必须补测':
            st = '、'.join(f'{k}={v}' for k, v in (c['case_status'] or {}).items()) or '-'
            w(f'| {c["name"]} | {p["title"]} | {c["category"]} | {", ".join(c["cases"]) or "-"} | {st} | {c["note"]} |')
for p in inv['pages']:
    for c in p['controls']:
        if c['coverage_verdict'] == '未覆盖' and c['gap_tier'] == '必须补测':
            w(f'| {c["name"]} | {p["title"]} | {c["category"]} | - | - | {c["note"]} |')
w('')
w('### 3.2 需夹具（页面/区块存在，但当前环境无对应数据）')
w('')
w('| 页面/区块 | 路由 | 关联用例 | 原因 |')
w('|---|---|---|---|')
for f in inv['fixture_required']:
    w(f'| {f["title"]} | `{f["route"]}` | {", ".join(f["cases"]) or "-"} | {f["reason"]} |')
w('')
w('### 3.3 需授权（写操作控件：本轮只读审计未触发，效果以既有用例为准；如需在本权威复验需授权）')
w('')
w('| 控件 | 页面 | 分类 | 既有用例 |')
w('|---|---|---|---|')
_seen = set()
for p in inv['pages']:
    for c in p['controls']:
        if c['category'] == '破坏性':
            k = (p['title'], c['name'])
            if k in _seen:
                continue
            _seen.add(k)
            w(f'| {c["name"]} | {p["title"]} | {c["category"]} | {", ".join(c["cases"]) or "-"} |')
w('')
w('> 说明：提交类控件（保存/创建/上传/开始传输/部署/立即运行/生成安装命令）均已有既有用例覆盖其效果，'
  '本轮只读审计不重复触发；如需在 repair 权威上重新验证，需明确授权。')
w('')
w('### 3.4 无控件（能力缺失，非覆盖缺口）')
w('')
w('| 项 | 关联用例 | 说明 |')
w('|---|---|---|')
for k in inv['known_absent_controls']:
    w(f'| {k["item"]} | {", ".join(k["cases"]) or "-"} | {k["note"]} |')
w('')
w('## 4. 跨页联动覆盖表')
w('')
w('| # | 联动路径 | 本轮结果 | 用例 | 缺口 |')
w('|---|---|---|---|---|')
for x in inv['cross_page_linkage']:
    w(f'| {x["id"]} | {x["flow"]} | {x["result"]} | {", ".join(x["cases"]) or "-"} | {x.get("gap") or "无"} |')
w('')
w('## 5. DataTable 复用页风险结论')
w('')
r = inv['datatable_reuse_risk']
w(f'**已知缺陷**：{r["defect"]}')
w('')
w(f'**成因（代码级）**：{r["mechanism"]}')
w('')
w('代码证据：')
for e in r['code_evidence']:
    w(f'- `{e}`')
w('')
w(f'**当前具备行选择的页面**：{", ".join(r["pages_with_row_selection"])}')
w('')
w(f'**当前不具备行选择的复用页**：{", ".join(r["pages_without_row_selection"])}')
w('')
w(f'**结论**：{r["verdict"]}')
w('')
w('只读核查手段：')
for e in r['readonly_checks']:
    w(f'- {e}')
w('')
w(f'**未验证项**：{r["not_verified"]}')
w('')
w('## 6. 环境与局限')
w('')
w('- 审计为**只读**：所有写操作控件（删除/保存/上传/开始传输/部署/立即运行/批量升级/撤销凭证等）均未点击；'
  '菜单与对话框打开后按 Esc 关闭并验证关闭成功。')
w('- 环境数据在审计期间被并发 QA 链清理：`/tasks` 从 16 条→0 条、`/groups`、`/schedules` 为空；'
  '`/executions` 1545 条、`/audit` 6918 条、`/transfers` 79 条、`/users` 8 条、`/nodes` 6 条。')
w('- 侧栏 shell 控件（导航/语言/主题/资料/折叠）统一记入 `global-shell` 页，避免在每页重复计数。')
w('- 菜单/对话框页只记录相对底层页面的**增量控件**。')
w('')
w('## 7. 证据索引')
w('')
w(f'- `control-inventory.json`：逐页控件清单（含分类、覆盖判定、用例映射）。')
w(f'- `raw/`：每页 `*.json`（DOM 控件提取）、`*.a11y.json`（可访问性快照）、`*.meta.json`（菜单/对话框文本）、'
  f'`nav-*.json`（跨页跳转实测）、`pages.txt`、采集脚本。')
w(f'- `evidence/`：{len([f for f in os.listdir(f"{OUT}/evidence") if f.endswith(".png")])} 张页面/菜单/对话框截图 + '
  f'任务列表行链接证据 `tasks-list-rowlinks-20261017.tsv`。')
w('')
open(f'{OUT}/control-coverage-audit.md', 'w', encoding='utf-8').write('\n'.join(L) + '\n')
print('written', f'{OUT}/control-coverage-audit.md', len(L), 'lines')
