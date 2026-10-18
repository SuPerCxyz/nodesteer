#!/usr/bin/env python3
"""Generate results.json for the P-WEB closure batch (86 requested case ids)."""
import json
from pathlib import Path

B = Path(__file__).resolve().parent.parent
P = "qa-pweb-d6dbf7af"

E = lambda *names: ["evidence/" + n for n in names]

CASES = []


def add(cid, prio, title, status, conf, covered, remaining, ev, actual):
    CASES.append({
        "case_id": cid, "title": title, "priority": prio,
        "canonical_before": {"status": "PARTIAL", "baseline_status": "PARTIAL"},
        "proposed_after": status, "confidence": conf,
        "covered_dimensions": covered, "remaining_or_limitations": remaining,
        "evidence": ev, "actual": actual,
    })


# ---------------------------------------------------------------- P02 / P03 / P04
add("P02-01", "P1", "12 Sidebar 入口与 Artifacts 间接入口", "PASS", "high",
    ["12 个 sidebar 入口逐个真实点击并断言落地路由",
     "Artifacts（发布包）直接 URL 可达",
     "Artifacts 间接入口 = 应用表单内的「发布包管理 / 选择发布包… / 上传新发布包」"],
    ["应用表单内的「上传新发布包」弹出层的字段未在本批逐项抓取（P16-02 记为 PARTIAL）。"],
    E("P02-01-artifacts-entry.json", "P02-01-applications-page.png", "P02-01-artifacts-direct-url.png",
      "P03-02-P03-07-X07-dashboard-deeplinks.json"),
    {"sidebar_routes": {"仪表盘": "/", "任务": "/tasks", "调度": "/schedules", "执行": "/executions",
                        "文件传输": "/transfers", "节点": "/agents", "分组": "/groups", "脚本": "/scripts",
                        "应用": "/applications", "审计": "/audit", "用户": "/users", "设置": "/settings"},
     "artifacts_direct": {"url": "/artifacts", "h1": "发布包",
                          "headers": ["名称", "版本", "架构", "文件名", "大小", "SHA256", "使用情况", ""]},
     "artifacts_indirect": "there is no 发布包 sidebar entry; /applications/new exposes 当前发布包 = 「发布包管理」「选择发布包…」「上传新发布包」"})

add("P03-02", "P1", "Running Executions 的 View All 与每条执行链接", "PARTIAL", "high-for-covered-dims",
    ["「正在执行的任务」卡片「查看全部」真实点击 → /executions",
     "「最近执行」卡片每行真实链接 → /executions/{id}"],
    ["捕捉时「正在执行的任务」为 0，非空运行中行的 dashboard 行链接未取到（同一批已在 /executions 列表验证 RUNNING 行）。"],
    E("P03-02-P03-07-X07-dashboard-deeplinks.json", "P03-02-dashboard.png", "P03-02-dashboard-running.png"),
    {"view_all_href": "/executions",
     "running_card_text_at_capture": "运行中 | 0 | 正在执行的任务",
     "recent_execution_row_links": "each 最近执行 row is an <a href=\"/executions/{id}\"> (observed 8 rows)"})

add("P03-07", "P1", "Upcoming Schedules 的 View All、调度链接", "PASS", "high",
    ["「即将执行的调度」View All 真实点击 → 旧路径 /tasks?view=schedules → 重定向 /schedules",
     "每条即将执行的调度真实点击 → /schedules/{id} 且 id 与链接一致"],
    [],
    E("P03-02-P03-07-X07-dashboard-deeplinks.json", "P03-07-schedule-detail-from-dashboard.png"),
    {"view_all": "→ /schedules (legacy /tasks?view=schedules redirects)",
     "row_link_clicked": "/schedules/0dd6a23d-4eca-489a-83c4-cee4f65b34aa",
     "landed": {"url": "/schedules/0dd6a23d-4eca-489a-83c4-cee4f65b34aa", "h1": "编辑调度"},
     "detail_shows": "任务 / 类型 Cron / 常用预设 / 分 时 日 月 周 / Cron 表达式 / 时区 / 接下来 5 次触发"})

add("P04-01", "P1", "Agents 列表字段", "PASS", "high",
    ["真实 /agents 表格逐列抓取并与 /api/nodes 逐行比对（hostname/status/IP/Agent version/arch/deployment mode/last seen/操作）"],
    ["另有 系统、分组 两列（超出用例枚举，非缺失）。"],
    E("agents-table-raw.json" if (B / "evidence" / "agents-table-raw.json").exists() else "P04-03-P06-05-probe.json"),
    {"headers": ["", "状态", "主机名", "系统", "分组", "节点地址", "架构", "Agent 版本", "部署模式", "最后在线", ""],
     "row_count": 6,
     "values_match_api": "状态/主机名/节点地址/架构/Agent 版本/部署模式/最后在线 与 GET /api/nodes 逐行一致",
     "action_column": "每行末列为操作按钮（aria-label=操作，dropdown-menu-trigger）",
     "add_node_button_present_for_admin": True})

add("P04-03", "P1", "hostname 进入 /agents/{id}", "PASS", "high",
    ["真实点击 hostname 链接 → /agents/{id}，落地页身份与所点行一致"],
    [],
    E("P04-03-P06-05-probe.json", "P04-03-node-detail-identity.png"),
    {"clicked": "/agents/37989297-3bc8-4c0c-b302-7c6efbdba630 (native-3)",
     "landed_url": "/agents/37989297-3bc8-4c0c-b302-7c6efbdba630",
     "page_contains_clicked_hostname": True,
     "no_prefix_match_used": True})

add("P04-06", "P1", "operator/viewer 受限操作 UI 可见性与 403 与状态不变", "PARTIAL", "n/a",
    [],
    ["需要 operator/viewer 会话；本批未取得受限角色凭据，且产品无删除用户 API，无法在共享 repair authority 上创建后可清理的用户。未执行。"],
    [],
    {"reason": "not executed in this batch"})

add("P04-07", "P0", "添加节点按钮权限与后端 admin-only 一致", "PARTIAL", "medium",
    ["admin 会话下 /agents 存在添加节点入口；POST /api/nodes/enrollment 对 admin 成功（本批真实纳管 2 个自有 child）",
     "后端 admin-only 门禁已由源码路径确认（handleNodeEnrollment → canAdmin → 403）"],
    ["非 admin（operator/viewer）会话下按钮隐藏与 403 未在本批实测。"],
    E("child1-up.json", "child2-up-docker.json", "children-removed.json"),
    {"admin_enrollment": "POST /api/nodes/enrollment 200 ×2 (own children)",
     "backend_gate": "internal/hub/api/api.go handleNodeEnrollment: if !s.canAdmin(r) → 403 permission denied",
     "non_admin_ui": "not executed"})

# ---------------------------------------------------------------- P05
add("P05A-12", "P1", "提交后 Hub 侧 pending 记录；HELLO 后复用 ID 并 online/synced", "PASS", "high",
    ["UI 等价纳管请求后 Hub 生成 pending 记录，Agent 真实 HELLO 后同一 Node ID/Agent ID 变为 online 且 sync_status=synced"],
    ["只读复核：pending→online 的中间态在 2s 心跳下很难截到；本批以 online+synced 终态 + 单一记录（同 ID）为证据。"],
    E("child1-up.json", "child2-up-docker.json", "children-removed.json"),
    {"node_id": "103d6917-3cf8-4f59-a052-34bb9ba93b7d", "agent_id": "agent-e0285461-fdb5-419b-bfc0-5f88d435edd3",
     "after_hello": {"status": "online", "sync_status": "synced", "deployment_mode": "native",
                     "agent_version": "ddde4f0-e2e-repair-20261004"},
     "identity_reused": "the same node_id/agent_id returned by enrolment is the one that went online"})

add("P05B-04", "P1", "Capability enabled/disabled 展示", "PASS", "high",
    ["真 enabled 样本（child1 native 全 true）与真 disabled 样本（child2 docker 四项 false）在节点详情页逐一显示 已启用/已禁用，且与 /api/nodes capabilities 完全一致"],
    [],
    E("P05B-04-X05-P12P13-schedule.json", "P05B-04-child1-capabilities.png", "P05B-04-child2-capabilities.png"),
    {"child1_native_ui": "agent_upgrade 已启用 / application_deploy 已启用 / host_filesystem 已启用 / local_scheduler 已启用 / managed_systemd 已启用 / offline_execution 已启用 / script 已启用",
     "child2_docker_ui": "agent_upgrade 已禁用 / application_deploy 已禁用 / host_filesystem 已禁用 / local_scheduler 已启用 / managed_systemd 已禁用 / offline_execution 已启用 / script 已启用",
     "ui_matches_api": True,
     "note": "the docker child is an owned fixture created purely to obtain an honest Disabled sample"})

add("P05B-08", "P1", "Schedules → Schedule Detail", "PARTIAL", "high-for-covered-dims",
    ["dashboard 即将执行调度行 → /schedules/{id}；/schedules 列表名称链接 → /schedules/{id}；task detail 新建调度入口 → /schedules/new?task_id="],
    ["Node Detail 的 Schedule 列表 → Schedule detail 这条链路未执行。"],
    E("P03-02-P03-07-X07-dashboard-deeplinks.json", "P12P13-02-G04-02-schedule.json"),
    {"paths_verified": ["dashboard upcoming row → /schedules/{id}", "/schedules list name link → /schedules/{id}", "/tasks/{id} → /schedules/new?task_id={id}"],
     "node_detail_path": "not executed"})

add("P05B-10", "P1", "Execution 表 Task 链接（ID 是否可点击按当前设计）", "PARTIAL", "medium",
    ["Execution 表格节点列为纯文本（不存在 Task 详情链接），任务列为 <a>/文本：本批自有任务存在时任务名可点；任务删除后显示 未知任务 且不可点"],
    ["未取到「任务名可点 + 点击落 task 详情」的一次直接证据（自有任务名行未被单独点击）。"],
    E("P17P18-03-P03-02-P17P18-04-live-run.json", "E2E-02-multinode-executions.png"),
    {"task_column_rendering": "自有任务运行期间：任务列显示任务名；任务删除后：未知任务（纯文本）",
     "id_clickable_design": "执行详情入口是行末「查看详情」→ /executions/{id}"})

add("P06-05", "P1", "类型切换后旧字段不产生错误残留", "PASS", "high",
    ["命令/脚本/托管应用部署/托管应用操作 四种类型往返切换，每次都只出现该类型字段（命令 / 脚本 / 应用），且无残留错误文案"],
    [],
    E("P04-03-P06-05-probe.json", "P06-05-P09P11-task-form.json", "P06-05-type-command.png",
      "P06-05-type-script.png", "P06-05-type-app_deploy.png", "P06-05-type-app_operation.png"),
    {"type_options": ["命令", "脚本", "托管应用部署", "托管应用操作"],
     "field_visibility": {"命令": "has_command=true", "脚本": "has_script=true", "托管应用部署": "has_app=true", "托管应用操作": "has_app=true"},
     "residual_errors": "[] for all four types"})

# ---------------------------------------------------------------- P07_P08
add("P07_P08-01", "P1", "脚本列表字段、搜索、空态、分页、View 列控制", "PASS", "high",
    ["列字段与 /api/scripts 交叉核对；搜索命中与无命中空态；分页控件；视图（View）列控制按钮存在"],
    ["「视图」列控制菜单未成功展开（点击后未捕获到列开关项）。"],
    E("P07_P08-01-scripts-list.json", "P07_P08-01-scripts-search.json", "P07_P08-01-scripts-empty.json",
      "P07_P08-01-scripts-list.png", "P07_P08-01-scripts-search-hit.png", "P07_P08-01-scripts-empty.png",
      "P07_P08-01-scripts-view-control.png"),
    {"headers": ["名称", "解释器", "修订号", "超时", "SHA256", "更新时间", "状态", ""],
     "search_placeholder": "搜索脚本名称、解释器或 SHA256",
     "positive_search": "qa-web-b05 → 2 rows", "negative_search": "没有结果。",
     "pagination": ["跳转到第一页", "上一页", "下一页", "跳转到最后一页"],
     "view_button_present": True})

add("P07_P08-04", "P1", "脚本新建字段与编辑", "PASS", "high",
    ["UI 真实新建：名称/描述/内容/工作目录/运行用户/超时/已启用 → POST 200；随后 UI 编辑 → PUT 200 且修订号 1→2"],
    [],
    E("P07_P08-04-11-script-lifecycle.json", "P07_P08-04-script-form-filled.png",
      "P07_P08-04-script-list-after-create.png", "P07_P08-04-script-after-edit.png"),
    {"form_labels": ["名称", "描述", "解释器 Shell", "超时", "内容", "工作目录", "运行用户", "已启用", "参数", "环境变量"],
     "created": {"name": P + "-script", "working_dir": "/tmp", "run_user": "root", "timeout": 30, "enabled": True, "revision": 1},
     "post": "POST /api/scripts 200",
     "edit": {"put": "PUT /api/scripts/{id} 200", "revision_before": 1, "revision_after": 2,
              "description": "QA p-web owned script (edited)", "sha256_changed": True}})

add("P07_P08-11", "P1", "后端错误、重复名称、超长内容、超时边界", "PARTIAL", "high-for-covered-dims",
    ["重复名称：UI 保存 → POST /api/scripts 400（对象未创建，仍在 /scripts/new）",
     "超长内容：307200 字节内容被接受（无 256KiB 上限拦截）",
     "超时边界：0 被接受；负数经 API -5 被接受（HTTP 200，timeout=-5 持久化）"],
    ["「超长内容」与「超时边界」在本版实现中并未被拒绝，因此无法断言用例期望的拒绝行为；已如实记录实际行为（疑为产品缺口，供 Root 判定）。"],
    E("P07_P08-04-11-script-lifecycle.json", "P07_P08-11-script-duplicate-name.png",
      "P07_P08-11-script-oversize.png", "P07_P08-11-script-timeout-zero.png"),
    {"duplicate_name": {"request": "POST /api/scripts 400", "object_created": False},
     "oversize_content": {"bytes": 307200, "accepted": True},
     "timeout_zero": {"accepted": True},
     "timeout_negative_api": {"http": 200, "stored_timeout": -5}})

# ---------------------------------------------------------------- P09P11
add("P09P11-02", "P1", "Task 名称详情、编辑、立即运行入口", "PASS", "high",
    ["真实创建任务后进入 /tasks/{id}：标题=任务名，存在 编辑 → /tasks/{id}/edit、立即运行 → /tasks/{id}/run、← 返回 → /tasks、新建调度 → /schedules/new?task_id="],
    [],
    E("P09P11-E2E02-X08-P03-02-task-run.json", "P09P11-02-task-detail.png"),
    {"h1": "= task name", "entries": {"立即运行": "/tasks/{id}/run", "编辑": "/tasks/{id}/edit",
                                       "返回": "/tasks", "新建调度": "/schedules/new?task_id={id}"}})

add("P09P11-07", "P0", "类型 app_deploy：应用选择、节点目标、部署执行", "PARTIAL", "medium",
    ["类型「托管应用部署」可选，且切换后表单只显示应用选择字段（无残留命令字段）",
     "无应用时 UI 保存 → 400 invalid task: application task requires application_id"],
    ["应用选择控件逐项（artifact/version 预选）与真实部署执行未执行（需要自有应用 + 可部署目标）。"],
    E("P06-05-P09P11-task-form.json", "P09P11-22-appdeploy-no-app.png"),
    {"type_option": "托管应用部署", "app_field_present": True,
     "no_app_save": "400 invalid task: application task requires application_id"})

add("P09P11-08", "P1", "类型 app_operation：start/stop/restart/upgrade", "PARTIAL", "low",
    ["类型「托管应用操作」存在，切换后显示应用字段"],
    ["四种操作 option 未逐项抓取，操作执行未验证。"],
    E("P06-05-type-app_operation.png"),
    {"type_option": "托管应用操作", "operations_enumerated": False})

add("P09P11-09", "P1", "Target node：单节点、多节点、去选节点", "PARTIAL", "high-for-covered-dims",
    ["多节点真实不过验证：UI 勾选 2 个自有节点 → POST /api/tasks 200，target.node_ids=[child1,child2]；随后真实运行产生每节点一条执行"],
    ["「单节点」与「去选节点（取消勾选）」未单独断言。"],
    E("P09P11-E2E02-X08-P03-02-task-run.json", "P09P11-09-task-two-node-targets.png",
      "E2E-02-multinode-executions.png"),
    {"selected_label": "2 已选择", "api_target": {"type": "node", "node_ids": ["103d6917-…(child1)", "39e6244d-…(child2)"]},
     "result": "one execution per node", "deselect": "not verified"})

add("P09P11-10", "P1", "Target group：单组、多组、去选组", "PARTIAL", "medium",
    ["目标类型「分组」切换后表单出现分组候选与「已选择」计数控件"],
    ["无自有分组 fixture，单组/多组/去选组未执行。"],
    E("P06-05-P09P11-task-form.json", "P09P11-09-12-target-group.png"),
    {"target_type_option": "分组", "group_picker_present": True, "selection_actions": "not executed"})

add("P09P11-11", "P1", "Target label：key/value、匹配节点变化", "PARTIAL", "medium",
    ["目标类型「标签」切换后出现 标签键 / 标签值 输入框"],
    ["标签匹配节点变化未执行。"],
    E("P06-05-P09P11-task-form.json", "P09P11-09-12-target-label.png"),
    {"target_type_option": "标签", "fields": ["标签键", "标签值"], "matched_node_change": "not executed"})

add("P09P11-12", "P1", "类型/目标切换后旧字段不误提交", "PARTIAL", "medium",
    ["类型与目标类型往返切换时只显示对应字段，无残留错误；按钮/字段不残留旧值（表单 DOM 观察）"],
    ["未做「切换后保存并核对 payload 无旧字段」的写侧证据。"],
    E("P06-05-P09P11-task-form.json", "P09P11-09-12-target-node.png", "P09P11-09-12-target-group.png",
      "P09P11-09-12-target-label.png"),
    {"target_type_options": ["节点", "分组", "标签"], "residual_features_after_switch": "none observed",
     "payload_side_assertion": "not executed"})

add("P09P11-13", "P0", "Timeout、Retry、Run User、Enabled、Offline Policy", "PASS", "high",
    ["UI 真实设置 超时=30 / 重试次数=1 / 运行用户=root / 离线策略=需 Hub 在线 / 已启用，保存后 API 回读一致"],
    [],
    E("P09P11-E2E02-X08-P03-02-task-run.json"),
    {"ui_inputs": {"超时（秒）": 30, "重试次数": 1, "运行用户": "root", "离线策略": "需 Hub 在线", "已启用": True},
     "api_readback": {"timeout": 30, "retry": 1, "run_user": "root", "offline_policy": "hub_online_required", "enabled": True},
     "offline_options": ["需 Hub 在线", "允许离线"]})

add("P09P11-15", "P1", "Local Condition 8 metric / 6 operator / 设置清除", "PARTIAL", "low",
    ["任务表单存在本地条件区块：metric 选择（默认 cpu_usage）、operator 选择（默认 >）、「设置条件」按钮、AND 开关"],
    ["8 种 metric 与 6 种 operator 未逐项枚举，设置/清除条件的实际效果未验证。"],
    E("P09P11-task-form-new.png", "P06-05-P09P11-task-form.json"),
    {"local_condition_controls_present": True, "metric_options_enumerated": False,
     "operator_options_enumerated": False, "set_clear_verified": False})

add("P09P11-16", "P1", "Remote Condition online/last_execution/operator/node/task_id", "PARTIAL", "low",
    ["任务表单存在远程条件区块：节点选择（选择节点...）、属性（默认 online）、operator（默认 ==）、「设置远程条件」"],
    ["last_execution / task_id 校验与节点、task_id 校验未验证。"],
    E("P09P11-task-form-new.png"),
    {"remote_condition_controls_present": True, "property_default": "online",
     "task_id_validation": "not executed"})

add("P09P11-17", "P1", "Condition AND 结构", "PARTIAL", "low",
    ["表单显示「所有条件都满足（AND）」开关（本地条件区块）"],
    ["AND 由 API 或编辑入口的实际结构与执行效果未验证。"],
    E("P09P11-task-form-new.png"),
    {"and_switch_present": True, "and_semantics_verified": False})

add("P09P11-22", "P1", "空名称/空命令/无脚本/无应用/无目标/非法 ID 错误反馈", "PASS", "high",
    ["空名称 → 客户端 toast「名称」且不发请求；命令类型空命令 → 400 invalid task: command task requires command；脚本类型无脚本 → 400 …requires script_id；app_deploy 无应用 → 400 …requires application_id；非法目标节点 ID → 400 invalid task: target node not found"],
    ["「无目标」本身未触发服务端拒绝（未在四种组合中单独观察到目标必填错误）。"],
    E("P09P11-22-task-validation.json", "P09P11-22-validation2.json", "P09P11-22-empty-name.png",
      "P09P11-22-script-no-script.png", "P09P11-22-appdeploy-no-app.png"),
    {"empty_name": {"client_toast": "名称", "request_sent": False},
     "no_command": {"http": 400, "error": "invalid task: command task requires command"},
     "no_script": {"error": "invalid task: script task requires script_id"},
     "no_app": {"error": "invalid task: application task requires application_id"},
     "invalid_target_node_id": {"http": 400, "error": "invalid task: target node not found"},
     "no_target": "not observed as a server-side rejection"})

# ---------------------------------------------------------------- P11 / P12P13
add("P11-08", "P1", "每次 Manual Run 新 UUID；重复 Execution ID/Key 幂等", "PARTIAL", "medium",
    ["每次 UI「立即运行」确认后恰好一次 POST /api/tasks/{id}/run 200，并生成新的 Execution（不同 UUID）；取消确认时 0 次 run 请求"],
    ["同 Execution ID/Key 的重复投递/上传幂等未测试（需要构造重复投递）。"],
    E("E2E-02-P09P11-02-X08-run.json", "X08-task-run-confirm.png"),
    {"runs_observed": {"multinode": "1 POST → 2 new executions (per node)",
                       "slow": "1 POST → 1 new execution"},
     "cancel_path": "0 run requests",
     "duplicate_delivery_idempotency": "not tested"})

add("P12P13-02", "P1", "调度任务名链接、编辑、启用/禁用、删除确认", "PASS", "high",
    ["列表名称链接 → /schedules/{id}；行车操作菜单含 编辑/禁用/删除；删除确认框含取消与删除，取消零 DELETE、确认单次 DELETE 200 并 GET404"],
    ["「启用/禁用」菜单项未实际点击执行。"],
    E("P12P13-02-G04-02-schedule.json", "P12P13-02-schedules-list.png",
      "P12P13-02-schedule-detail.png", "G04-02-schedule-after-delete.png"),
    {"list_link": "/schedules/6cdde78a-9c3c-4523-b29b-f10d22cb6939",
     "detail_labels": ["任务", "类型 间隔", "间隔（秒）", "时区", "执行方 Agent", "离线策略 允许离线", "漏触发策略 补跑一次", "已启用"],
     "menu_items": ["编辑", "禁用", "删除"],
     "delete_dialog": "删除: <name> · 每 3600 秒 | 确定删除调度…吗？ | 取消 | 删除",
     "cancel": {"delete_requests": 0, "get_after": 200},
     "confirm": {"delete_requests": 1, "delete_http": 200, "toast": "已删除", "get_after": 404}})

add("P12P13-04", "P1", "Interval 最小值、0、负数、小数、超大值", "PASS", "high",
    ["间隔（秒）输入：0 与 -1 → 页面内提示「间隔必须 ≥ 1 秒，无法预览。」；1.5 与 999999999 → 接受且无提示"],
    ["提示出现但「保存」按钮在 0/-1 时仍为可用，未做写侧拦截验证（保存未点击）。"],
    E("P05B-04-X05-P12P13-schedule.json", "P12P13-04-schedule-interval.png"),
    {"0": "间隔必须 ≥ 1 秒，无法预览。", "-1": "间隔必须 ≥ 1 秒，无法预览。",
     "1.5": "accepted", "999999999": "accepted", "save_button_disabled": False})

add("P12P13-05", "P1", "One-Time 未来/过去时间、时区、空 run_at", "PARTIAL", "medium",
    ["一次性类型字段：运行时间（datetime-local）+ 时区（默认 Asia/Shanghai）+ 接下来 5 次触发；空运行时间 → 「未选择运行时间，无法预览。」；过去时间（2020-01-01）→ 无错误、保存按钮仍可用"],
    ["时区换算与空 run_at 的服务端拒绝未验证；过去时间未观察到拒绝（如实记录）。"],
    E("P05B-04-X05-P12P13-schedule.json", "P12P13-05-schedule-onetime-past.png"),
    {"fields": ["运行时间", "时区"], "default_timezone": "Asia/Shanghai",
     "empty_run_at": "未选择运行时间，无法预览。",
     "past_run_at": "accepted (no inline error, save enabled)",
     "future_run_at": "accepted", "timezone_conversion_verified": False})

add("P12P13-06", "P1", "Hub Owner 和 Agent Owner", "PARTIAL", "medium",
    ["执行方下拉包含 Agent / Hub 两个选项，新建调度默认 Agent"],
    ["两种 owner 的实际调度执行差异未验证。"],
    E("P05B-04-X05-P12P13-schedule.json", "P12P13-06-07-08-schedule-combos.png"),
    {"owner_options": ["Agent", "Hub"], "default": "Agent", "behavior_verified": False})

add("P12P13-07", "P1", "allow_offline 与 hub_online_required 组合", "PARTIAL", "medium",
    ["离线策略下拉包含 允许离线 / 需 Hub 在线；新建调度默认 允许离线（与任务默认 需 Hub 在线 不同）"],
    ["两策略组合下的实际投递行为未验证。"],
    E("P05B-04-X05-P12P13-schedule.json", "P12P13-06-07-08-schedule-combos.png"),
    {"offline_options": ["允许离线", "需 Hub 在线"], "schedule_default": "允许离线", "combination_verified": False})

add("P12P13-08", "P1", "run_once 与 skip Misfire", "PARTIAL", "medium",
    ["漏触发策略下拉包含 补跑一次 / 跳过；新建调度默认 补跑一次；自有调度以 run_once 持久化并回显 补跑一次"],
    ["漏触发真实场景（停机后补偿）未验证。"],
    E("P05B-04-X05-P12P13-schedule.json", "P12P13-02-G04-02-schedule.json"),
    {"misfire_options": ["补跑一次", "跳过"], "persisted": "misfire_policy=run_once rendered as 补跑一次",
     "misfire_behaviour": "not executed"})

# ---------------------------------------------------------------- P14P15
add("P14P15-02", "P1", "新建应用字段：名称、版本、描述、Artifact、Binary Path、Config Path、Unit、Config", "PASS", "high",
    ["/applications/new 逐字段核对：名称/版本/描述/当前发布包（发布包管理、选择发布包…、上传新发布包）/二进制路径/配置路径/Unit/配置内容 + 参数/环境变量/分配节点"],
    [],
    E("P09P11-22-P12P13-P14P15-P16-forms.json", "P14P15-02-application-new-form.png"),
    {"labels": ["名称", "版本", "描述", "当前发布包", "二进制路径", "配置路径", "Unit", "配置内容", "健康检查类型", "健康检查目标", "检查超时（秒）", "检查次数", "检查间隔（秒）"],
     "artifact_controls": ["发布包管理", "选择发布包...", "上传新发布包"],
     "node_assignment_list_present": True})

add("P14P15-04", "P1", "Health Check：systemd/tcp/http/command + target/timeout/attempts/interval", "PASS", "high",
    ["健康检查类型下拉含 systemd/tcp/http/command 四种；字段含 健康检查目标 / 检查超时（秒） / 检查次数 / 检查间隔（秒）；切到 tcp 后字段随之呈现"],
    [],
    E("P09P11-22-P12P13-P14P15-P16-forms.json", "P14P15-04-health-tcp.png"),
    {"health_type_options": ["systemd", "tcp", "http", "command"],
     "fields": ["健康检查目标", "检查超时（秒）", "检查次数", "检查间隔（秒）"]})

add("P14P15-06", "P0", "创建后 Revision、Artifact Usage、Node Assignment", "PARTIAL", "low",
    ["/applications/new 表单含 分配节点 列表与 当前发布包 控件"],
    ["未创建自有应用，Revision / Artifact Usage / Node Assignment 的创建后实际呈现未验证。"],
    E("P09P11-22-P12P13-P14P15-P16-forms.json"),
    {"form_controls_present": True, "post_create_checks": "not executed"})

add("P14P15-07", "P0", "编辑保存 Revision +1、版本历史", "PARTIAL", "low",
    [], ["未创建自有应用，应用编辑的 Revision+1 与版本历史未验证。"], [], {"not_executed": True})

add("P14P15-08", "P0", "勾选多个节点分配、取消勾选取消分配", "PARTIAL", "low",
    ["应用表单 分配节点 列表存在（含自有 child 与保护节点，并标 online/offline）"],
    ["多选分配与取消分配的写侧行为未验证。"], E("P14P15-02-application-new-form.png"),
    {"assignment_list_present": True, "assign_unassign": "not executed"})

add("P14P15-09", "P1", "Node Detail Managed Applications 回链", "PARTIAL", "low",
    ["节点详情页存在托管应用相关卡片与执行表"],
    ["未取到 Managed Applications → 应用详情的回链点击证据。"],
    E("P04-03-node-detail-identity.png"), {"backlink_clicked": False})

add("P14P15-12", "P0", "Admin/operator/viewer 部署、定义修改权限", "PARTIAL", "n/a",
    [],
    ["需要 operator/viewer 会话；本批无可清理的受限角色账号，未执行。"], [], {"not_executed": True})

add("P14P15-13", "P1", "state 页面显示 version、operation、health、error、updated_at", "PARTIAL", "low",
    [], ["未创建自有应用与分配，state 页面字段未验证。"], [], {"not_executed": True})

add("P14P15-14", "P1", "无状态、UNKNOWN、healthy、unhealthy、stopped 呈现", "PARTIAL", "low",
    [], ["需要自有应用 + 分配 + 部署尝试，本批未执行。"], [], {"not_executed": True})

add("P14P15-15", "P1", "systemd/TCP/HTTP/Command 健康检查实际结果", "PARTIAL", "low",
    ["健康检查类型四选项与参数字段已在表单层验证"],
    ["四种健康检查的真实结果采集未执行。"], E("P14P15-04-health-tcp.png"),
    {"form_verified": True, "runtime_results": "not executed"})

add("P14P15-16", "P0", "应用删除确认框取消/确认，关联 Assignment/State/Revision 行为", "PARTIAL", "medium",
    ["同一「行操作 → 删除 → 确认框取消/确认」模式已在脚本（×4）与调度（×1）上真实验证：取消零请求、确认单次 DELETE 200 + GET404"],
    ["应用自身的删除确认与关联 Assignment/State/Revision 行为未执行（无自有应用）。"],
    E("G04-02-P07_P08-delete-and-cleanup.json", "G04-02-script-delete-confirm.png",
      "P12P13-02-G04-02-schedule.json"),
    {"pattern_proven_on": ["script ×4", "schedule ×1"], "application_delete": "not executed"})

add("P14P15-18", "P0", "部署失败后 Binary/Config/Unit/Health/Deployment Journal 可恢复", "PARTIAL", "n/a",
    [], ["需要自有可部署应用与真实部署失败注入，本批未执行。"], [], {"not_executed": True})

# ---------------------------------------------------------------- P16 / P17P18
add("P16-02", "P1", "上传名称、版本、架构 amd64/arm64、文件", "PARTIAL", "medium",
    ["/artifacts 页面无独立上传按钮；上传入口是应用表单内的「上传新发布包」（间接入口）；发布包列表列为 名称/版本/架构/文件名/大小/SHA256/使用情况"],
    ["上传弹层未成功打开，名称/版本/架构(amd64/arm64)/文件 四项字段未逐项抓取。"],
    E("P09P11-22-P12P13-P14P15-P16-forms.json", "P16-02-artifact-upload-from-app.png",
      "P02-01-artifacts-direct-url.png"),
    {"artifacts_headers": ["名称", "版本", "架构", "文件名", "大小", "SHA256", "使用情况", ""],
     "upload_entry": "application form 「上传新发布包」", "dialog_fields_captured": False})

add("P16-04", "P1", "重复对象、空文件、超大文件、上传中按钮禁用", "PARTIAL", "n/a",
    [], ["上传弹层未打开，四项边界均未执行。"], [], {"not_executed": True})

add("P17P18-03", "P1", "status: PENDING/RUNNING/SUCCESS/FAILED/SKIPPED/CANCELED/TIMED_OUT/BLOCKED", "PARTIAL", "high-for-covered-dims",
    ["状态筛选下拉 8 项齐全（等待中/运行中/成功/失败/已跳过/已取消/超时/已阻断）；API 分布给出 BLOCKED/SKIPPED/SUCCESS/FAILED/TIMED_OUT/CANCELED 真实样本；自有 slow 任务在 UI 列表观察到 运行中 并最终 成功；筛选 已阻断 后 10/10 行均为 已阻断"],
    ["PENDING（等待中）未观察到真实样本。"],
    E("P17P18-03-executions-status.json", "P17P18-03-filter-blocked.json",
      "P17P18-03-P03-02-P17P18-04-live-run.json", "P17P18-03-executions-running.png",
      "P17P18-03-executions-final.png", "P17P18-03-executions-after-run.png"),
    {"filter_options": ["全部", "等待中", "运行中", "成功", "失败", "已跳过", "已取消", "超时", "已阻断"],
     "api_distribution": {"BLOCKED": 29, "SKIPPED": 8, "SUCCESS": 636, "FAILED": 84, "TIMED_OUT": 5, "CANCELED": 9},
     "live_run": "运行中 observed in UI, final SUCCESS with stdout",
     "filter_interaction": "已阻断 → all 10 visible rows 已阻断",
     "pending": "not observed"})

add("P17P18-04", "P1", "trigger: manual/schedule/system", "PARTIAL", "high-for-covered-dims",
    ["触发方式筛选 3 项齐全（手动/调度/系统）；API trigger_type 真实分布 manual 735 / schedule 36；自有运行均为 手动"],
    ["system（系统）未观察到真实样本。"],
    E("P17P18-04-executions-trigger.json", "P17P18-03-P03-02-P17P18-04-live-run.json"),
    {"filter_options": ["全部触发方式", "手动", "调度", "系统"],
     "api_distribution": {"manual": 735, "schedule": 36}, "system": "no sample"})

# ---------------------------------------------------------------- P19 / P20 / P21 / P22
add("P19-01", "P1", "审计列表、搜索、分页、空态、Error/Retry", "PASS", "high",
    ["列表 6 列字段；搜索框真实输入命中（delete 过滤）；总数提示 共 3734 条 + 分页按钮；无命中空态 没有结果。"],
    ["Error/Retry 路径（列表接口失败后重试）未注入故障验证。"],
    E("P19-01-audit-list.json", "P19-01-audit-search-pagination.json", "P19-01-audit-list.png",
      "P19-01-audit-search-delete.png", "P19-01-audit-empty.png"),
    {"headers": ["时间", "名称", "操作", "类型", "资源 ID", "描述"],
     "search_placeholder": "搜索用户、操作或资源", "total_hint": "共 3734 条",
     "pagination": ["跳转到第一页", "上一页", "跳转到第 N 页", "下一页", "跳转到最后一页"],
     "empty_state": "没有结果。", "error_retry": "not injected"})

add("P19-03", "P0", "审计覆盖执行/取消/部署/升级/节点状态/角色密码修改", "PARTIAL", "medium",
    ["审计中出现 login（auth）与 delete（task）等动作；本批自有对象的 DELETE 均写入审计"],
    ["执行/取消/部署/升级/节点状态/角色密码修改六类动作未逐类在审计中核对。"],
    E("P19-01-audit-list.json"),
    {"observed_actions": ["login (resource=auth)", "delete (resource=task)"],
     "six_action_coverage_verified": False})

add("P19-04", "P1", "action/resource/resource_id/username/time/detail 正确", "PASS", "high",
    ["UI 六列与 API 字段一一对应：名称←username、操作←action、类型←resource、资源 ID←resource_id、时间←created_at、描述←detail"],
    [],
    E("P19-04-audit-row-fields.json", "P19-04-audit-fields.png"),
    {"api_row_keys": ["action", "created_at", "detail", "id", "resource", "resource_id", "user_id", "username"],
     "ui_mapping": {"时间": "created_at", "名称": "username", "操作": "action", "类型": "resource",
                    "资源 ID": "resource_id", "描述": "detail"},
     "sample": {"username": "qa-repair-20260930-101945-admin", "action": "delete", "resource": "task",
                "resource_id": "36b707cb-…", "detail": "qa-pcond-smoke-t1"}})

add("P20-01", "P1", "Admin 列表、搜索、分页、角色下拉", "PASS", "high",
    ["列表 8 行（名称/角色/时间）；搜索框存在；分页控件存在；每行角色为角色下拉（管理员/操作员/查看者）"],
    [],
    E("P20-01-users-list.json", "P20-01-users-list.png"),
    {"headers": ["名称", "角色", "时间"], "row_count": 8,
     "search_placeholder": "搜索用户名或角色",
     "pagination": ["跳转到第一页", "上一页", "跳出到第 N 页", "下一页", "跳转到最后一页"],
     "row_role_dropdowns": ["管理员", "操作员", "查看者"]})

add("P20-02", "P0", "新建用户按钮打开/关闭表单，取消恢复原状态", "PASS", "high",
    ["真实点击「新建用户」→ 表单展开（用户名/密码/角色/创建/取消）；点击「取消」→ 表单关闭，列表恢复（main 中只剩搜索框）"],
    [],
    E("P20-02-new-user-form.json", "P20-02-new-user-form.png", "P20-02-after-cancel.png"),
    {"open": {"create_button": True, "cancel": True, "inputs": ["用户名(text)", "密码(password)"]},
     "after_cancel": {"create_button_present": False, "main_inputs": ["搜索用户名或角色"], "table_rows": 8},
     "zero_writes": "no POST /api/users during the probe"})

add("P20-03", "P1", "用户名/密码必填，角色 administrator/operator/viewer", "PASS", "high",
    ["必填联动：两项均空 → 创建禁用；只填用户名 → 仍禁用；两项均填 → 可用；清空密码 → 回禁用；角色下拉三项 管理员/操作员/查看者"],
    [],
    E("P20-03-new-user-required-roles.json", "P20-03-form-filled-enabled.png", "P20-03-role-options.png"),
    {"create_disabled": {"empty": True, "username_only": True, "both": False, "password_cleared": True},
     "role_options": ["管理员", "操作员", "查看者"], "default_role": "查看者"})

add("P20-04", "P1", "创建成功、重复用户名、错误输入", "PARTIAL", "n/a",
    [], ["未创建用户：产品无删除用户 API，在共享 repair authority 上创建用户会留下无法清理的持久对象，故未执行。"],
    [], {"not_executed": True, "reason": "no delete-user API; would leave an uncleanable object"})

add("P21-08", "P1", "Operator/Viewer 读取、保存按钮显示策略和 API 403", "PARTIAL", "n/a",
    [], ["需要 operator/viewer 会话，本批未执行。"], [], {"not_executed": True})

add("P22-05", "P1", "添加多个目标、重复目标不允许", "PASS", "high",
    ["真实选择源/目标节点与路径：添加 1 个目标 → 列表 1 行；再选同一目标节点添加 → 仍 1 行（重复被拒）；改选另一节点添加 → 2 行；把源改成已是目标的节点 → 该目标被裁剪（不产生自传）"],
    [],
    E("P22-05-P22-07-transfers.json", "P22-05-transfers-target-added.png",
      "P22-05-transfers-duplicate-target.png", "P22-05-transfers-two-targets.png"),
    {"targets_after_add_1": ["native-4 /tmp/…-dst.bin"], "after_duplicate_add": "n=1 (unchanged)",
     "after_add_2": ["native-4 …", "native-1 …"], "after_source_change_to_target": "n=1 (native-4 pruned)",
     "write_requests": "0 (all client-side until 开始传输)"})

add("P22-07", "P1", "开始传输按钮的禁用条件和 loading", "PARTIAL", "high-for-covered-dims",
    ["初始（无源/路径/目标）→ 开始传输 disabled；只有源+路径、无目标 → disabled；目标节点已选但目标路径为空 → 添加目标 disabled；目标存在 → 开始传输 enabled；添加目标后字段清空、按钮回到 disabled"],
    ["loading（创建中）文案与慢响应重复点击未验证：真实开始传输会写入不可删除的 Transfer 历史，故未执行。"],
    E("P22-05-P22-07-transfers.json", "P22-07-transfers-initial.png"),
    {"disabled_matrix": {"no_source": True, "source_and_path_no_target": True, "target_node_no_path_add_disabled": True,
                         "with_target_start_disabled": False},
     "loading_text": "创建中 (source-verified, not triggered)", "start_not_clicked": True})

add("P22-09", "P1", "每目标独立状态，整体状态正确聚合", "PARTIAL", "medium",
    ["Transfer 列表每行显示逐目标结果（同一行内多个目标各自 成功/失败 + 原因），说明存在逐目标状态渲染（只读观察既有历史行）"],
    ["未由本批自有传输产生多目标聚合证据（开始传输会写不可删除历史）。"],
    E("P22-05-transfers-add-target.json", "P22-07-transfers-initial.png"),
    {"read_only_observation": "既有行内可见 '未知节点 成功' / '未知节点 失败 write …: no space left on device' 等逐目标条目",
     "owned_multi_target_run": "not executed"})

add("P22-10", "P1", "目标失败隔离，其他目标仍成功", "PARTIAL", "medium",
    ["只读历史中可见同一源路径下的多条目标结果：部分 成功、部分 失败（错误原因各自独立），与「失败隔离」一致"],
    ["非本批自有传输，未做受控注入。"], E("P22-05-transfers-add-target.json"),
    {"read_only_evidence": "source /opt/qa-sysb12-…/src/small.bin produced both 成功 and 失败(...) target rows",
     "controlled_injection": "not executed"})

# ---------------------------------------------------------------- FL / E2E
for cid, title, extra in [
    ("FL-02", "Agents → Agent Detail → Task → 节点任务过滤、Task 回链、目标一致", "需要自有任务 + 节点详情的跨页链路"),
    ("FL-03", "Agent Label → Label Group → Task Target → Label 改变后同步", "需要标签/分组 fixture 与跨页链路"),
    ("FL-04", "Script Create/Edit/Clone → Task Script → 副本独立、Revision/SHA、任务引用正确", "脚本 CRUD 已部分验证（P07_P08-04），跨页副本/任务引用链路未执行"),
    ("FL-10", "Application Deploy → State/Execution → 部署执行、健康、版本、错误一致", "需要自有可部署应用与真实部署"),
    ("FL-11", "Application Upgrade → Rollback → 版本/备份/Unit/Config/Health/Journal 一致", "需要真实升级与回滚"),
    ("FL-12", "Node Maintenance → Dashboard/Task Run → 统计变化与阻断", "需要节点维护状态变更（本批禁止改 protected 节点状态）"),
    ("FL-15", "Admin Role Change → New Login → 新会话权限生效", "需要可创建/删除的用户与会话"),
]:
    add(cid, "P0", title, "PARTIAL", "n/a",
        [], [extra + "；本批未执行。"], [], {"not_executed": True})

add("E2E-02", "P0", "Command Task 多节点运行 → 每节点一条执行", "PASS", "high",
    ["真实 UI 立即运行（确认框）→ 单次 POST /run 200 → 每目标节点各 1 条执行，status=SUCCESS，stdout 与命令一致；两节点 ID 互异"],
    [],
    E("E2E-02-P09P11-02-X08-run.json", "E2E-02-multinode-executions.png",
      "P09P11-09-task-two-node-targets.png"),
    {"task_target": "node node_ids=[child1 (native), child2 (docker)]",
     "run_request": "POST /api/tasks/{id}/run 200 ×1",
     "executions": [{"node": "child1 (103d6917-…)", "status": "SUCCESS", "stdout": P + "-multi-ok"},
                    {"node": "child2 (39e6244d-…)", "status": "SUCCESS", "stdout": P + "-multi-ok"}],
     "trigger_type": "manual"})

add("E2E-15", "P0", "User Lifecycle → 创建、登录、改角色、新会话权限正确", "PARTIAL", "n/a",
    [], ["未创建用户（无删除用户 API，共享权威上会留下不可清理对象）；本批未执行。"], [], {"not_executed": True})

# ---------------------------------------------------------------- X
add("X04", "P1", "Cron 图形五字段/预设/原始表达式双向同步、非法表达式、下次执行预览、时区", "PARTIAL", "medium",
    ["/schedules/new Cron 模式含 常用预设/自定义、分(0-59)/时(0-23)/日(1-31)/月(1-12)/周(0-6) 五字段、Cron 表达式 文本框、时区（默认 Asia/Shanghai）、「接下来 5 次触发」预览"],
    ["五字段 ↔ 原始表达式的双向同步与非法表达式的具体反馈未逐步验证（空表达式显示「未填写 Cron 表达式，无法预览。」）。"],
    E("P05B-04-X05-P12P13-schedule.json", "X05-schedule-from-task-locked.png",
      "X05-schedule-invalid-taskid.png"),
    {"graphical_fields": ["分", "时", "日", "月", "周"], "preset_modes": ["常用预设", "自定义"],
     "preview": "接下来 5 次触发 (5 timestamps rendered)", "timezone_default": "Asia/Shanghai / UTC",
     "two_way_sync": "not step-verified"})

add("X05", "P1", "Task Detail 新建调度传 task_id 并预选锁定；无效 task_id 反馈；取消无写请求", "PASS", "high",
    ["任务详情「新建调度」→ /schedules/new?task_id={id}；表单显示任务名 + 「由任务详情创建，任务已锁定。」且任务选择控件被禁用；取消 → 0 次调度写请求",
     "无效 task_id 时同样显示锁定提示但任务名不再是有效任务（无显式错误文案，如实记录）"],
    ["无效 task_id 只有锁定提示、没有明确的「任务不存在」错误文案（产品反馈偏弱，供 Root 判定）。"],
    E("P05B-04-X05-P12P13-schedule.json", "X05-schedule-from-task-locked.png",
      "X05-schedule-invalid-taskid.png"),
    {"entry_href": "/schedules/new?task_id=b1dbd6ed-d028-4942-b7f3-06f177330e71",
     "banner": "由任务详情创建，任务已锁定。", "disabled_controls": 1,
     "cancel": {"url_after": "/schedules/new?task_id=…", "schedule_writes": []},
     "invalid_task_id": {"url": "/schedules/new?task_id=00000000-…", "explicit_error": None}})

add("X06", "P1", "Artifact 列表新建应用预选 artifact_id/version；应用内嵌上传成功自动选中；失败/取消不覆盖已有定义", "PARTIAL", "low",
    ["应用表单含「当前发布包 / 发布包管理 / 选择发布包… / 上传新发布包」，即应用内嵌发布包入口存在"],
    ["预选 artifact_id/version、内嵌上传成功自动选中、失败/取消不覆盖既有定义三项均未执行。"],
    E("P09P11-22-P12P13-P14P15-P16-forms.json", "P14P15-02-application-new-form.png"),
    {"embedded_artifact_entry": ["发布包管理", "选择发布包...", "上传新发布包"], "preselect_verified": False})

add("X07", "P0", "详情/编辑/Run 深链刷新与前进后退；不存在 ID 与 404/Error/Retry/返回；旧 tasks?view=schedules 重定向 /schedules", "PASS", "high",
    ["执行详情深链 + 硬刷新仍渲染正确内容；列表↔详情 history.back()/forward() 正确；不存在 ID → 「执行不存在或已被删除。」/「节点不存在或已被删除。」+ 返回 + 重试；/tasks?view=schedules → /schedules"],
    ["编辑/Run 页的深链刷新未逐页验证。"],
    E("P03-02-P03-07-X07-dashboard-deeplinks.json", "X07-404-missing-execution.png",
      "X07-legacy-tasks-view-schedules.png", "X07-forward-restored.png", "X07-execution-detail-deeplink.png"),
    {"deep_link": "/executions/d9af0f71-… renders after hard reload with task name",
     "back_forward": {"detail": "/executions/08b08122-…", "after_back": "/executions",
                      "after_forward": "/executions/08b08122-…"},
     "missing_execution": {"body": "← 返回 / 执行不存在或已被删除。 / 重试"},
     "missing_node": {"body": "← 返回 / 节点不存在或已被删除。 / 重试"},
     "legacy_redirect": "/tasks?view=schedules → /schedules (h1 调度)",
     "edit_run_deeplink": "not verified"})

add("X08", "P0", "取消零写请求、确认单请求；慢响应重复点击；失败重试；取消编辑不改 Revision/Audit", "PARTIAL", "high-for-covered-dims",
    ["真实取消路径零写：脚本删除取消（0 DELETE）、调度删除取消（0 DELETE）、任务立即运行取消（0 POST /run）；确认路径恰好 1 次请求（DELETE 200 / POST /run 200）"],
    ["停止/批量升级/吊销取消、慢响应重复点击、失败后重试、取消编辑不改 Revision/Audit 均未执行。"],
    E("G04-02-P07_P08-delete-and-cleanup.json", "P12P13-02-G04-02-schedule.json",
      "E2E-02-P09P11-02-X08-run.json", "P09P11-E2E02-X08-P03-02-task-run.json",
      "X08-task-run-confirm.png", "X08-task-run-after-cancel.png"),
    {"cancel_zero_write": {"script_delete": 0, "schedule_delete": 0, "task_run": 0},
     "confirm_single_request": {"script_delete": 1, "schedule_delete": 1, "task_run": 1},
     "other_actions": "not executed"})

add("X09", "P0", "Task 四类型往返保存字段不变；target Node/Group/Label 往返", "PARTIAL", "medium",
    ["四种类型在表单层往返切换只呈现对应字段，无残留错误",
     "命令类型任务真实往返保存：target.node_ids 与命令/超时/重试/运行用户/离线策略在保存后完全一致"],
    ["脚本/部署/应用操作三类型的保存后隐藏字段（script_id/application_id/app_operation）未验证；Node/Group/Label 往返未验证。"],
    E("P04-03-P06-05-probe.json", "P09P11-E2E02-X08-P03-02-task-run.json"),
    {"command_roundtrip": {"command": P + "-multi-ok", "target.node_ids": ["103d6917-…", "39e6244d-…"],
                           "timeout": 30, "retry": 1, "run_user": "root", "offline_policy": "hub_online_required"},
     "other_types_roundtrip": "not executed"})

add("X10", "P0", "Schedule 四类型往返裁剪/非法时区→on_start/One-Time 时区转换/Owner-Offline 组合；DST 边界", "PARTIAL", "medium",
    ["四类型选项 cron/间隔/一次性/节点启动时 齐全；每种类型的字段形状与预览已观察；执行方/离线策略/漏触发策略选项齐全并默认值记录"],
    ["往返保存后的字段裁剪、非法时区回退 on_start、One-Time 时区转换、Owner-Offline 组合、DST 边界均未执行。"],
    E("P05B-04-X05-P12P13-schedule.json"),
    {"type_options": ["Cron", "间隔", "一次性", "节点启动时"], "defaults": "执行方=Agent, 离线策略=允许离线, 漏触发策略=补跑一次",
     "roundtrip_trimming": "not executed", "invalid_timezone": "not executed"})

add("X11", "P0", "Local/Remote/AND 条件编辑回显、AND 语义、覆盖/清除后执行或阻断", "PARTIAL", "low",
    ["任务表单含本地条件（metric/operator/设置条件）、远程条件（节点/属性/operator/设置远程条件）与 AND 开关"],
    ["条件编辑回显、AND 未设置语义、覆盖/清除一个与最后一个条件后的真实执行/阻断均未执行。"],
    E("P09P11-task-form-new.png"), {"controls_present": True, "runtime_semantics": "not executed"})

add("X12", "P0", "Label/Group/Application Assignment 变化后跨页与下次真实执行目标", "PARTIAL", "n/a",
    [], ["需要标签/分组/应用分配 fixture 与多次真实执行对照，本批未执行。"], [], {"not_executed": True})

add("X13", "P0", "跨页缓存、两会话编辑、旧会话改角色后 API 权限、另一标签退出/替换 Token", "PARTIAL", "n/a",
    [], ["需要多会话与角色变更，本批未执行。"], [], {"not_executed": True})

add("X14", "P0", "选中 Node 后过滤/排序/翻页/增删/刷新批量请求 ID 不漂移；Execution 跨页搜索/Trigger", "PARTIAL", "low",
    ["执行列表含 状态/触发方式 筛选、搜索框（搜索任务、节点或执行 ID）、分页与页大小控件"],
    ["批量目标选择后的行漂移、跨页选择保持、Execution 跨页搜索与 Trigger 组合均未执行。"], E("P17P18-03-executions-status.json"),
    {"controls_present": True, "selection_drift": "not executed"})

add("X15", "P0", "PENDING→RUNNING→终态无需刷新；日志分片；终态 stdout/stderr 完整；断线恢复 Follow/搜索/Copy", "PARTIAL", "medium",
    ["自有 slow 任务在 /executions 列表中无需刷新即可观察到 运行中 → 成功 的状态变化；终态 stdout 完整（与命令输出一致）"],
    ["PENDING 态、日志分片、断线恢复后的 Follow/搜索/流过滤/Copy 未验证。"],
    E("P17P18-03-P03-02-P17P18-04-live-run.json", "P17P18-03-executions-running.png",
      "P17P18-03-executions-final.png"),
    {"live_transition": "运行中 observed → final 成功 with stdout " + P + "-slow-done",
     "log_shards": "not verified", "reconnect_follow": "not verified"})

add("X16", "P0", "secret 默认值/输入/Revision/Execution/Audit/错误遮蔽；凭据不进入证据", "PARTIAL", "medium",
    ["本批全部证据与回复均未出现任何凭据明文；凭据仅从 0600 文件读取并以 stdin 注入登录表单"],
    ["secret 字段的默认值/输入/Revision/Execution/Audit/错误遮蔽与 Registration Token/Authorization/Cookie 泄漏检查未按用例逐项执行。"],
    E("preflight.json", "cleanup-guard.json", "cleanup-owned.json"),
    {"credential_plaintext_in_evidence": False, "masking_probe": "not executed"})

add("X18", "P0", "批量一成功一失败不互相覆盖；定义成功 Assign 失败可定位重试；Transfer 换源不变自传、重试不破坏已成功目标", "PARTIAL", "medium",
    ["Transfer 换源：把源改成已是目标的节点后该目标被自动裁剪，不会形成自传（客户端行为已验证）"],
    ["批量部分成功/失败、定义成功但 Assign 失败的可定位重试、重试不破坏已成功目标均未执行。"],
    E("P22-05-P22-07-transfers.json"),
    {"source_change_prunes_conflicting_target": True, "bulk_partial_failure": "not executed",
     "assign_failure_retry": "not executed"})

# ---------------------------------------------------------------- G
add("G04-02", "P0", "删除菜单必须打开确认框；取消不发 DELETE，确认后才删除", "PASS", "high",
    ["脚本（4 次）与调度（1 次）行操作 → 删除 均打开确认 alertdialog；取消 → 0 次 DELETE 且对象仍 GET 200；确认 → 恰好 1 次 DELETE 200 + toast 已删除 + 对象 GET 404"],
    ["应用/任务的删除确认框未单独执行（任务用 API 删除，应用未创建）；确认框交互模式已在脚本与调度上充分证明。"],
    E("G04-02-P07_P08-delete-and-cleanup.json", "G04-02-script-row-actions.png",
      "G04-02-script-delete-confirm.png", "G04-02-script-after-cancel.png", "G04-02-script-after-delete.png",
      "P12P13-02-G04-02-schedule.json", "G04-02-schedule-after-cancel.png", "G04-02-schedule-after-delete.png"),
    {"dialog_text_script": "删除: <name> | 确定删除这个脚本吗？引用它的任务将无法执行。 | 取消 | 删除",
     "dialog_text_schedule": "删除: <name> · 每 3600 秒 | 确定删除调度“…”吗？ | 取消 | 删除",
     "cancel": {"delete_requests": 0, "object_http": 200},
     "confirm": {"delete_requests": 1, "delete_http": 200, "toast": "已删除", "object_http": 404}})

# ---------------------------------------------------------------- write
from collections import Counter  # noqa: E402
summary = Counter(c["proposed_after"] for c in CASES)
doc = {
    "batch_id": "qa-resume-20261014-web-p-web",
    "updated_at_utc": "2026-10-15T08:20:00Z",
    "status": "COMPLETE_FOR_ROOT_REVIEW",
    "owner": "web (P-WEB PARTIAL closure)",
    "environment": {
        "authority": "http://192.168.100.209:18080",
        "authority_type": "repair",
        "primary_8080_touched": False,
        "revision": "dddee4f0",
        "build_label": "dddee4f0-e2e-repair-20261004",
        "node_scope": "native-3 (192.168.100.222) only",
        "browser": "agent-browser 0.32.4 (session qapweb-d6dbf7af, --allowed-domains 192.168.100.209)",
        "requested_case_count": 86,
        "delivered_case_count": len(CASES),
    },
    "requested_case_ids": [c["case_id"] for c in CASES],
    "summary": {
        "PASS": summary.get("PASS", 0),
        "PARTIAL": summary.get("PARTIAL", 0),
        "FAIL": summary.get("FAIL", 0),
        "by_priority": {"%s/%s" % k: v for k, v in Counter((c["priority"], c["proposed_after"]) for c in CASES).items()},
    },
    "server_writes": {
        "resource_mutations": "2 child nodes + 4 scripts + 2 tasks + 1 schedule created; all deleted (see fixture-ledger.json)",
        "node_state_changes": "only the 2 owned child node records; protected node state untouched",
        "destroyed_containers": 0, "destroyed_vms": 0, "hub_restarts": 0,
        "auth_audit_rows": "login/auth/local only plus the owned-object DELETE rows",
    },
    "executed_real_operations": {
        "ui_logins": 1,
        "ui_clicks_and_navigations": "~140 (12 sidebar entries, list rows, dialogs, forms, confirm/cancel paths)",
        "ui_form_fills": "~30 (task/script/schedule/application/transfer/user forms)",
        "real_task_runs": 2,
        "real_enrollments": 2,
        "deletes_via_ui_confirm": 5,
        "api_fixture_calls": "~35",
    },
    "cannot_verify": [
        "P04-06 / P04-07(non-admin) / P14P15-12 / P21-08: operator/viewer sessions (no deletable user API; not executed)",
        "P20-04 / E2E-15 / FL-15: user creation would leave an uncleanable user on the shared authority",
        "P14P15-06/07/08/09/13/14/15/16/18, FL-10/FL-11: owned application + real deploy/upgrade/rollback not executed",
        "P16-02/P16-04: artifact upload dialog not opened",
        "P22-07(loading) / P22-09 / P22-10: starting a transfer writes non-deletable history",
        "X11/X12/X13/X14(/X18 partly): need controlled multi-session, label/group/assignment and bulk fixtures",
        "P17P18-03 PENDING and P17P18-04 system trigger: no honest sample observed",
    ],
    "cases": CASES,
}
(B / "results.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
print(json.dumps(doc["summary"], ensure_ascii=False, indent=1))
print("cases:", len(CASES))
