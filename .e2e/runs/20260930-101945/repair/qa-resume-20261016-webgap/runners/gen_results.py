#!/usr/bin/env python3
"""生成 results.json / progress.json，并把 fixture-ledger / final-cleanup-guard 复制到批次根目录。"""
import json
import shutil
import time
from pathlib import Path

BATCH = Path(__file__).resolve().parent.parent
EV = BATCH / "evidence"

for f in ("fixture-ledger.json", "final-cleanup-guard.json"):
    shutil.copy(EV / f, BATCH / f)

results = {
    "batch_id": "qa-resume-20261016-webgap",
    "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "status": "COMPLETE",
    "owner": "web (WEB-GAP-20261016 小缺口清扫)",
    "environment": {
        "authority": "http://192.168.100.209:18080",
        "authority_type": "repair",
        "primary_8080_touched": False,
        "verified_build": "dddee4f0-e2e-repair-20261016",
        "agent_version_observed": "dddee4f0-e2e-repair-20261016 (all online nodes)",
        "node_scope": "native-2 (192.168.100.206) only",
        "browser": "agent-browser 0.32.4 (session qawebgap-6456ada2)",
        "transfers_appended_retained": 4,
        "executions_appended_retained": 3,
    },
    "cases": {
        "X04": {
            "status": "PASS", "confidence": "high",
            "remaining_dimension": "五字段 ↔ 原始表达式双向同步与非法表达式具体反馈",
            "evidence": ["evidence/X04-cron-sync.json", "evidence/X04-02-raw-valid.png",
                         "evidence/X04-04-invalid.png", "evidence/X04-05-field-invalid.png",
                         "evidence/X04-06-nongraphical.png", "evidence/X04-07-preview.png",
                         "evidence/X04-09-preset.png"],
            "summary": "raw→5 字段回填（'30 4 1-5 * 2'）、字段→raw 重生成（分=7 → '7 4 1-5 * 2'；时=*/3 → '7 */3 1-5 * 2'）、非法 raw（aria-invalid + role=alert「Cron 表达式无效」+ 字段不回填不清空 + 预览「Cron 表达式无效，无法预览。」）、字段级非法（月=13 → 该字段 aria-invalid + raw 重生成并联动告警）、非图形化（@daily → 五字段 disabled + 「该表达式不支持图形化编辑，可继续手动输入。」且预览正常）、预览 5 条时间戳 + 时区：Asia/Shanghai、预设切换（每天 12:00 → '0 12 * * *'）；未提交任何表单（POST /api/schedules 计数 0）。",
            "observations": ["空表达式初始态同时出现 role=alert「Cron 表达式无效」与「未填写 Cron 表达式，无法预览。」；空值语义更接近“未填写”，提示文案可选优化（低严重度，非阻断）。"],
            "unverified": []
        },
        "X20": {
            "status": "PARTIAL", "confidence": "high",
            "remaining_dimension": "长 ID/SHA/路径/参数/日志与安全剪贴板",
            "evidence": ["evidence/X20-long-values.json", "evidence/X20-attribution.json",
                         "evidence/X20-attribution2.json", "evidence/X20-01-exec-detail-1280.png",
                         "evidence/X20-16-artifacts-sha.png", "evidence/X20-18-schedules-new-480-long-name.png",
                         "evidence/X20-19-copy-logs-selection.png", "evidence/X20-20-script-detail-480.png"],
            "summary": "长日志 345,628 字符/3007 行在受控 <pre> 内滚动（clientH 560 / scrollH 72176），1280/760/480 均无页面级横向滚动；长 SHA（64 hex）在制品表截断为 17 字符并带完整 title；长路径（132 字符）在传输表截断 + title 全量；长 ID（执行详情 40 字符）正常；安全剪贴板：HTTP 非安全上下文点击「复制日志」→ 诚实失败提示「复制失败，请手动复制。」，不会误报「已复制」。",
            "findings_defect_candidates": [
                {"id": "X20-F1", "severity": "low", "page": "/schedules/{id}（调度编辑页）",
                 "desc": "任务名很长时选中项 Radix SelectTrigger 宽 898px（w-fit + whitespace-nowrap，无 max-width/truncate），760/480 视口出现页面级横向滚动（docScrollW 939/898 > innerW）。未选任务时 480 视口 docScrollW=480（隔离归因成立）。",
                 "evidence": "evidence/X20-attribution.json (x20-schedules-new-480-long-name-attribution), evidence/X20-18-schedules-new-480-long-name.png"},
                {"id": "X20-F2", "severity": "low", "page": "/scripts/{id}（脚本编辑页）",
                 "desc": "含超长脚本内容/长参数时，480 视口页面级横向滚动（docScrollW 676 > 480，卡片宽 660）；空表单 /scripts/new 在 480 下不溢出（docScrollW 448 链路全 ≤480），故与长值内容相关。760/1280 正常。",
                 "evidence": "evidence/X20-attribution2.json (x20-script-detail-480-attribution), evidence/X20-20-script-detail-480.png"},
            ],
            "verdict_note": "长 ID/SHA/路径/参数/日志与安全剪贴板主体行为已验证；新发现 2 处窄屏长值溢出（低严重度、窄屏限定）。若评审将「长值不得撑破布局」作为通过门槛，本属建议转 FAIL（见 findings_defect_candidates）。",
            "unverified": ["HTTPS 安全上下文下的自动剪贴板写入成功路径未验证（本环境为 HTTP，window.isSecureContext=false）。"]
        },
        "P16-02": {
            "status": "PASS", "confidence": "high",
            "remaining_dimension": "上传四项字段逐项抓取（此前误判为“弹层未打开”）",
            "evidence": ["evidence/P16-artifact-upload.json", "evidence/P16-artifact-upload-2.json",
                         "evidence/P16-02-01-form.png", "evidence/P16-02-04-after-upload.png",
                         "evidence/P16-02-05-artifacts-list.png", "evidence/P16-02-06-list-after-arm64.png"],
            "summary": "制品上入口是独立页面 /artifacts/new（不是弹层，历史“上传弹层未成功打开”为入口误判）。逐项抓取：名称 Input[name=name] required；版本 Input[name=version] required；架构 select[name=architecture] 选项 [amd64, arm64] 默认 amd64；文件 input#artifact-file type=file required（标签「选择文件」+ 选中后显示文件名）。真实 UI 上传 amd64（1 MiB，sha256=4eadedc9…cff7，size=1048576）与 arm64（选择 arm64 后上传，architecture=arm64、sha256 一致）均通过 API 复核。",
            "unverified": []
        },
        "P16-04": {
            "status": "PASS", "confidence": "medium",
            "remaining_dimension": "重复对象 / 空文件 / 超大文件 / 上传中按钮禁用",
            "evidence": ["evidence/P16-artifact-upload-2.json", "evidence/P16-04-01-large-selected.png",
                         "evidence/P16-04-02-uploading.png", "evidence/P16-04-03-uploading-2.png"],
            "summary": "重复对象：同名同版本再次真实上传 → 生成第二条独立记录（两条 id 不同、同 sha256），未拒绝、未覆盖。空文件：0 字节上传被接受（size=0，sha256=e3b0c442…b855）。超大文件：64 MiB 真实上传成功（size=67108864，sha256 与本地一致），前后端均无大小上限校验。上传中按钮禁用：采样到「上传中...」+ disabled=true（t=0.35s/0.75s），期间再次点击不产生第二次 POST（POST /api/artifacts 计数 1）。",
            "observations": ["制品上传无任何大小上限（前端无校验、Hub 侧 artifact Upload 未使用 MaxBytesReader/LimitReader），与文件传输的 10 GiB 上限不一致；超大文件不存在“拒绝边界”，建议作为独立产品/安全观察项评估（低-中严重度，非本用例失败）。"],
            "unverified": ["上传中断/重试、服务端写盘失败（ENOSPC）与并发同名上传未验证。"]
        },
        "P05B-08": {
            "status": "PASS", "confidence": "high",
            "remaining_dimension": "Node Detail 的 Schedule 列表 → Schedule detail",
            "evidence": ["evidence/P03-P05B-X20-node-links.json", "evidence/P05B-08-01-node-schedules.png",
                         "evidence/P05B-08-02-schedule-detail.png"],
            "summary": "native-2 节点详情页出现自有 schedule 链接 /schedules/bea1c6eb…（文本为任务名），点击后 SPA 落到 /schedules/bea1c6eb…（页面标题「编辑调度」，显示该任务、Cron 图形五字段、时区 Asia/Shanghai、接下来 5 次触发 2027–2031 年）。",
            "unverified": []
        },
        "P03-02": {
            "status": "PASS", "confidence": "high",
            "remaining_dimension": "非空「正在执行的任务」行的 dashboard 行链接",
            "evidence": ["evidence/P03-P05B-X20-node-links.json", "evidence/P03-02-01-dashboard-running.png"],
            "summary": "以自有任务在 native-2 制造真实 RUNNING 执行（API 复核 status=RUNNING）后抓取仪表盘：运行中计数=1，「正在执行的任务」区块不再显示空态，运行中行为链接 /executions/c5cd0511…（文本含任务名/native-2/运行中/3s）；「查看全部」链接指向 /executions。",
            "unverified": []
        },
        "P05B-10": {
            "status": "PASS", "confidence": "high",
            "remaining_dimension": "任务名可点 + 点击落 task 详情",
            "evidence": ["evidence/P03-P05B-X20-node-links.json", "evidence/P05B-10-01-node-exec-running.png",
                         "evidence/P05B-10-02-task-detail.png"],
            "summary": "native-2 节点详情执行表中，自有任务行任务列渲染为链接 /tasks/49259725…（运行中与失败两种状态行均是链接，未知任务仍为纯文本）；点击后进入 /tasks/49259725…，页面 h1 即任务名，含定义/目标/执行/调度区块。",
            "unverified": []
        },
        "P22-07": {
            "status": "PASS", "confidence": "high",
            "remaining_dimension": "loading（创建中）文案与慢响应重复点击",
            "evidence": ["evidence/P22-07-ui-start.json", "evidence/P22-07-ui-slow-loading.json",
                         "evidence/P22-07-01-start-enabled.png", "evidence/P22-07-02-restored.png",
                         "evidence/P22-07-06-slow-ready.png", "evidence/P22-07-07-loading-slow.png"],
            "summary": "禁用矩阵：初始/仅选源/仅填源路径 → 开始传输 disabled；目标+目标路径加入后再启用；清空源路径立即回到 disabled（反向路径）。loading：在页面内包装 window.fetch 对 POST /api/transfers 注入 2s 延迟（请求仍真实发往后端）后，真实 DOM 观测到「创建中...」+ disabled=true；加载期间再次点击不产生第二次请求（POST /api/transfers 计数 1，传输总数 68→69）。无注入时该 POST <25ms 完成，loading 文案不可采样（如实记录）。",
            "unverified": ["未验证真实后端慢响应（>1s）场景；慢响应由客户端延迟注入模拟（方法已在证据中声明）。"]
        },
        "P22-09": {
            "status": "PASS", "confidence": "high",
            "remaining_dimension": "自有传输的多目标聚合证据（含 FAILED 分支）",
            "evidence": ["evidence/P22-transfers.json", "evidence/P22-09-10-transfers-list.png",
                         "evidence/P22-09-11-list-search.png"],
            "summary": "自有多目标传输 6e976e92（源 native-2 实文件 → 目标 native-2 合法路径 + native-4 非法目标路径）：目标逐个独立呈现 FAILED（「mkdir /etc/hostname: not a directory」）与 SUCCESS（size/sha 一致），整体聚合 FAILED（allTerminal && anyFailed 分支）。第二条 24934964（源文件不存在）→ 整体 FAILED 且全部目标 FAILED（源上传失败 failTransfer 分支）。列表中同一传输内不同目标状态并存可见。",
            "unverified": []
        },
        "P22-10": {
            "status": "PASS", "confidence": "high",
            "remaining_dimension": "受控注入下的目标失败隔离（FAILED 侧可达性）",
            "evidence": ["evidence/P22-transfers.json", "evidence/P22-09-11-list-search.png"],
            "summary": "受控注入成立：同一传输内 native-4 目标因目标路径非法被 agent 回报 FAILED，同传输 native-2 目标 SUCCESS（文件真实落盘，size/sha 校验通过）→「其他目标仍成功」成立，且 FAILED 侧完全可达。故历史结论「FAILED 侧始终不可达/12 分钟挂 DELIVERING」在本修复版上用受控注入被证伪，无需转 FAIL；该历史现象应归因于当时 host001 目标端未回报，而非 Hub 聚合缺陷。",
            "verdict_note": "本条无需“建议转 FAIL”；相反建议将历史上因 FAILED 侧不可达而判 PARTIAL 的结论升级为 PASS（证据见上）。",
            "unverified": ["未复现历史上 host001（docker_host_integration 节点）只接包不回报的现象（本批未把 host001 设为目标，避免对其他批次节点产生副作用）。"]
        },
    },
    "recommended_status_changes": [
        {"case": "P22-10", "from": "PARTIAL", "to": "PASS", "reason": "受控注入证明 FAILED 侧可达且隔离成立"},
        {"case": "X20", "from": "PARTIAL", "to": "PARTIAL（附 2 条缺陷候选，建议评审决定是否转 FAIL）", "reason": "长值主体通过，但窄屏+长值出现 2 处页面级横向滚动"},
    ],
    "harness_notes": [
        "子 Agent 委派不可用：spawn 返回 'Subagent depth limit reached (1)'（当前会话已是子会话），本批全部工作由本会话串行完成。",
        "agent-browser eval 中的异步采样若触发 SPA 跳转，会以 'CDP error: Inspected target navigated or closed' 中断，需要 try/except 并在跳转后重新读取页面状态。",
        "制品上传入口是页面 /artifacts/new 而非弹层或对话框 —— 这解释了历史 P16-02/P16-04「上传弹层未成功打开」，属入口误判，不是产品缺陷。",
        "/transfers 表单中源路径与目标路径两个 input 使用同一 placeholder，按 placeholder 定位会命中源路径输入框，需按 DOM 顺序索引区分。",
        "布置脚本时若 working_dir 不存在，执行会立刻失败且 block_reason 为 'fork/exec /bin/sh: no such file or directory'（错误提示可读性偏低，属产品 UX 观察）。",
        "并发说明：本批窗口内 /api/executions 总数 +4，其中 3 条为本批（2 FAILED 失败尝试 + 1 SUCCESS），另 1 条来自并发批次，计数增量不能单独归因本批。",
    ],
    "cleanup": {
        "deleted_get404": True,
        "deleted": {"schedules": 1, "tasks": 1, "scripts": 1, "artifacts": 5},
        "node_side_removed": "/var/lib/qa-webgap-6456ada2 on 192.168.100.206 (verified gone)",
        "retained_no_delete_endpoint": {"transfers": 4, "executions": 3},
        "guard": "final-cleanup-guard.json",
        "ledger": "fixture-ledger.json",
    },
    "unverified_items": [
        "P16-04：制品上传不存在大小上限，因此“超大文件拒绝”路径无从验证（已用 64 MiB 证明被接受）。",
        "X20：HTTPS 安全上下文下的自动剪贴板写入成功路径未验证（环境为 HTTP）。",
        "P22-07：慢响应为客户端延迟注入，未验证真实后端慢响应。",
        "传输 retry（UI 重试按钮）与 cancel 未在本批触发，以避免新增不可删除历史。",
        "未做 375px 及更窄视口验证。",
    ],
}
(BATCH / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n")

progress = {
    "batch_id": "qa-resume-20261016-webgap",
    "owner": "web (WEB-GAP-20261016)",
    "status": "COMPLETE",
    "phase": "RESULTS_SAVED_8PASS_1PARTIAL_1PARTIAL_WITH_DEFECT_CANDIDATES",
    "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "session": "qawebgap-6456ada2",
    "authority": "http://192.168.100.209:18080",
    "authority_type": "repair",
    "primary_8080_management_writes": 0,
    "nonce": "6456ada2",
    "prefix": "qa-webgap-6456ada2",
    "node_scope": "native-2 (192.168.100.206) only",
    "verified_build": "dddee4f0-e2e-repair-20261016",
    "requested_case_count": 10,
    "delivered_case_count": 10,
    "results": {
        "PASS": ["X04", "P16-02", "P16-04", "P05B-08", "P03-02", "P05B-10", "P22-07", "P22-09", "P22-10"],
        "PARTIAL": ["X20"],
    },
    "recommend_fail_candidates": ["X20 (2 处窄屏长值溢出，低严重度，待评审)"],
    "cleanup": "DONE",
    "evidence_index": sorted(p.name for p in EV.iterdir()),
}
(BATCH / "progress.json").write_text(json.dumps(progress, ensure_ascii=False, indent=1) + "\n")
print("written results.json / progress.json / ledger / guard")
print(json.dumps(progress["results"], ensure_ascii=False))
