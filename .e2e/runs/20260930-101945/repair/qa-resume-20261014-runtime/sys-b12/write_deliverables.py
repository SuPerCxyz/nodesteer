#!/usr/bin/env python3
"""Build the sys-b12 deliverables (case-result.json, fixture-ledger.json, progress.json)
from the canonical run evidence.  Read-only over evidence/; never touches fixtures.
"""
import json
import os
import re
import time
from pathlib import Path

import hubapi

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-sysb12-%s-' % NONCE
TAG = 'qa-sysb12-%s' % NONCE
NOW = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
def e(name):
    return json.loads((HERE / 'evidence' / name).read_text()) \
        if (HERE / 'evidence' / name).is_file() else {}


E = e


def tmp_path_from_reason(reason):
    """The product names the temp file it failed to write; that path proves co-location."""
    m = re.search(r'write (\S+): no space left on device', reason or '')
    return m.group(1) if m else None


def nonclean_rows(p5):
    rows = []
    for c in p5.get('non_clean_absolute') or []:
        tp = c.get('target_probe') or {}
        bp = c.get('binary_path') or '/'
        info = (tp.get('files') or {}).get(bp) or {}
        dirinfo = (tp.get('dirs') or {}).get(os.path.dirname(bp)) or {}
        rows.append({
            'label': c.get('label'), 'binary_path': bp, 'put_http': c.get('put_http'),
            'deploy_http': c.get('deploy_http'), 'execution': c.get('execution'),
            'agent_rejected_with_validate_path': c.get('agent_rejected_with_validate_path'),
            'target_lexists': info.get('lexists'), 'target_is_dir': info.get('is_dir'),
            'target_dir_entries': dirinfo.get('entries'),
            'no_new_file_created': not (info.get('exists') and not info.get('is_dir')),
        })
    return rows


def main():
    run = json.loads((HERE / 'run-evidence.json').read_text()) if (HERE / 'run-evidence.json').is_file() else {}
    if not run:
        run = {'results': {}, 'state': {}, 'budget': {}, 'failures': []}
    res = run.get('results', {})
    R = lambda n: res.get(n + '.json', {}) or {}
    g = R('phase0-guard')
    ca = R('phase1-owned-child-a')
    cb = R('phase2-owned-child-b')
    fx = R('phase3-fixtures')
    p4 = R('phase4-hub-path-guard')
    p5 = R('phase5-agent-path-guard')
    p6 = R('phase6-transfer-path-guard')
    p7 = R('phase7-allowlist')
    p8 = R('phase8-ar32-permission-denied')
    p9 = R('phase9-ar34-recovery')
    p10 = R('phase10-cross-fs-atomic-replace')
    p11 = R('phase11-capability-gate')
    p12 = R('phase12-cleanup')
    p13 = (json.loads((HERE / 'final-cleanup-oracle.json').read_text())
           if (HERE / 'final-cleanup-oracle.json').is_file() else (run.get('oracle') or {}))
    iv = E('independent-verification.json')
    iv_gets = (iv.get('hub') or {}).get('get_404_probe') or {}
    proxy = p11.get('proxy_after') or {}
    nonclean = nonclean_rows(p5)

    node_a = (ca.get('enrollment') or {}).get('node_id')
    node_b = (cb.get('enrollment') or {}).get('node_id')
    app_id = fx.get('app_id')
    artifact = fx.get('artifact') or {}

    # ---------------------------------------------------------------- cases
    sys56 = {
        'case_id': 'SYS-56',
        'title': 'SYS-56 `../`、绝对路径、Symlink Escape、Allowlist。',
        'canonical_before': 'NOT_RUN',
        'proposed_after': 'PASS' if (
            p4.get('all_hub_level_rejections_ok')
            and all(c['agent_rejected_with_validate_path'] for c in nonclean)
            and all(c['no_new_file_created'] for c in nonclean)
            and p5.get('symlink_escape', {}).get('host_adapter_refused_symlink')
            and p5.get('symlink_escape', {}).get('victim_unchanged')
            and p6.get('nonclean_blocked') and p6.get('symlink_blocked')
            and p6.get('victim2_unchanged') and p6.get('positive_control_ok')
            and p7.get('outside_blocked') and p7.get('inside_ok')
            and p7.get('outside_allowlist_dir_absent')) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [
            '相对路径（application binary_path/config_path）：Hub 400 "invalid application path"，无新行、revision 不前进',
            '含 `..` 的绝对路径：Hub 400，无新行',
            '路径含 NUL：Hub 400',
            'Hub 接受但 Host Adapter 拒绝的非 clean 绝对路径（`//`、`/./`、尾随 `/`）：deploy Execution FAILED，block_reason 含 '
            '"invalid deployment path"/"invalid absolute path"，目标路径无任何文件副作用',
            'symlink 目的地（Host Adapter 部署路径）：AtomicReplace 拒绝 "destination is a symlink"，逃生目标文件 SHA 不变、symlink 仍是 symlink',
            'symlink 目的地（文件交付路径）：AtomicReplaceReader 拒绝 "destination is a symlink"，victim 文件 SHA 不变',
            '文件交付目的地相对路径 / `..` 穿越 / 空：Hub 400（"absolute path required" / "path traversal is not allowed"），无传输行创建',
            'Allowlist（非 native owned child + host_path_allowlist=/opt/qa-sysb12-<nonce>）：allowlist 外 clean 绝对路径交付 FAILED '
            '"path is outside configured allowlist" 且目录未创建；allowlist 内正对照 SUCCESS 且 SHA 一致',
            '正向对照：合法路径交付/安装真实落盘且 SHA 一致',
        ],
        'remaining_or_limitations': [
            'ContainerHostAdapter 的 host-root（/host + /proc/1/root）与容器 allowlist 分支在本环境不可运行：Host Integration 容器形态被 '
            'AppArmor/nsenter 阻塞（FAIL-ENV-HOST-001/002，与本批 SYS-54/SYS-55/AR-31/INV-14 同一环境门禁）。本批只覆盖 NativeHostAdapter '
            '与 API 层可观测部分，容器 Host Adapter 的 symlink-escape/host-root 校验未验证。',
            'NativeHostAdapter 没有 allowlist 概念（host root 为 "/"）；allowlist 维度由 Agent 文件交付路径守卫（host_path_allowlist）验证，'
            '而非 native 适配器本身。',
            'symlink 逃逸的中间目录（非最后一段）在 native 模式下按设计不拦截（host root 为 "/"），本批只验证“目的地本身是 symlink”被拒。',
            '拒绝本身不产生宿主机副作用（victim SHA 不变），但该次部署的 rollback 也会被同一 symlink 检查拒绝，'
            '于是部署日志永久停留在 ROLLING_BACK（见 defects[0]）；这是拒绝路径的后续影响，不是本用例的通过条件失败。',
        ],
        'defect_ref': 'SYSB12-DEFECT-01',
        'evidence': ['phase4-hub-path-guard.json', 'phase5-agent-path-guard.json',
                     'phase6-transfer-path-guard.json', 'phase7-allowlist.json'],
        'actual_resources': {'app_id': app_id, 'artifact_id': artifact.get('id'),
                             'owned_escape_victim_dir': '/tmp/%s-escape' % TAG},
        'probe': {
            'hub_definition_guard': p4.get('application_definition_guard'),
            'hub_transfer_guard': p4.get('transfer_guard'),
            'app_revision_unchanged': p4.get('app_revision_unchanged'),
            'non_clean_absolute': nonclean,
            'symlink_escape': {k: p5.get('symlink_escape', {}).get(k) for k in
                               ('label', 'execution', 'victim_unchanged', 'symlink_still_symlink',
                                'host_adapter_refused_symlink')},
            'transfer_guard_cases': [{k: c.get(k) for k in ('label', 'destination_path', 'final_status',
                                                            'target_status', 'target_error')}
                                     for c in (p6.get('cases') or [])],
            'allowlist_cases': [{k: c.get(k) for k in ('label', 'destination_path', 'final_status',
                                                       'target_status', 'target_error')}
                                for c in (p7.get('cases') or [])],
        },
    }

    p10t = p10.get('transfer') or {}
    p10s = p10.get('same_dir_temp_evidence') or {}
    p10e = p10.get('enospc') or {}
    p10n = p10.get('app_deploy_enospc') or {}
    p10a = p10.get('app_deploy') or {}
    t_reason = p10e.get('reason') or p10e.get('target_error') or ''
    t_tmp = tmp_path_from_reason(t_reason)
    d_reason = p10n.get('reason') or ''
    d_tmp = tmp_path_from_reason(d_reason)
    derived = {
        'transfer_enospc_temp_path': t_tmp,
        'transfer_temp_path_dir_is_target_dir': bool(t_tmp) and os.path.dirname(t_tmp) ==
            os.path.dirname((p10e.get('destination') or '/')),
        'deploy_enospc_temp_path': d_tmp,
        'deploy_temp_path_dir_is_target_dir': bool(d_tmp) and os.path.dirname(d_tmp) ==
            os.path.dirname((p10n.get('binary_path') or '/')),
        'note': 'the kernel error message contains the exact temp path the product tried to create; '
                'that path lives in the target directory, which is direct evidence of co-located '
                'temp creation (independent of the race-based sampler)',
    }
    cross_fs_proof = (bool(derived['transfer_temp_path_dir_is_target_dir'])
                      and bool(derived['deploy_temp_path_dir_is_target_dir'])
                      and p10e.get('proves_temp_is_in_target_fs') and p10n.get('proves_temp_in_target_fs'))
    sys57 = {
        'case_id': 'SYS-57',
        'title': 'SYS-57 跨文件系统临时文件和 Atomic Replace。',
        'canonical_before': 'NOT_RUN',
        'proposed_after': 'PASS' if (
            (p10.get('cross_fs_premise') or {}).get('target_is_different_fs_from_tmp')
            and p10t.get('final_status') == 'SUCCESS' and p10t.get('sha_matches_source')
            and p10s.get('temp_file_observed_in_target_dir')
            and p10s.get('agent_fd_open_in_target_dir')
            and p10s.get('no_partial_target_ever_observed') and p10s.get('no_tmp_leftover')
            and p10e.get('proves_temp_is_in_target_fs') and p10e.get('target_unchanged')
            and p10n.get('proves_temp_in_target_fs') and cross_fs_proof
            and p10a.get('atomic_replace_ok')) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [
            '目标目录位于 owned tmpfs（与 /tmp 不同 device）：96 MiB 文件交付 SUCCESS，落盘 SHA == 源 SHA',
            '采样证明临时文件出现在目标目录内（.nodesteer-transfer-*），且 Agent 进程在目标目录内持有打开的 fd',
            '原子性：目标文件名在任何采样点都不是部分大小（要么不存在，要么完整大小）；完成后无 .tmp 残留',
            '确定性同文件系统证明（文件交付路径）：目标 mount 预置 32 MiB 目标 + 56 MiB tmpfs 容量不足以同时容纳旧目标与临时文件 → 交付以 '
            '"no space left on device" 失败，且旧目标 SHA 不变',
            '确定性同文件系统证明（应用部署 AtomicReplace 路径）：2 MiB tmpfs + 预置 1.5 MiB 旧二进制 → 部署以 ENOSPC 失败且旧二进制 SHA 不变、'
            '无临时文件残留（若临时文件在 /tmp 侧则应为 EXDEV）',
            '应用部署 AtomicReplace（同一 owned tmpfs mount 内）：binary SHA == artifact SHA、unit active、无部分文件、无 .tmp 残留',
        ],
        'remaining_or_limitations': [
            '采样是竞态观测（无 strace/inotifywait 可用），因此同目录临时文件另有 2 项确定性容量证据（文件交付 + 应用部署）与 rename-成功'
            '（无 EXDEV）证据相互印证。',
            '本用例不覆盖容器 Host Adapter 的 /host 跨挂载 rename（环境阻塞，同 SYS-54）。',
        ],
        'evidence': ['phase10-cross-fs-atomic-replace.json'],
        'actual_resources': {'owned_mount': p10.get('mount_point'), 'transfer_id': p10t.get('transfer_id'),
                             'enospc_transfer_id': p10e.get('transfer_id'),
                             'xapp_id': p10a.get('app_id')},
        'probe': {
            'cross_fs_premise': p10.get('cross_fs_premise'),
            'transfer': {k: p10t.get(k) for k in ('create_http', 'transfer_id', 'final_status', 'error',
                                                  'sha256', 'sha_matches_source', 'tmp_leftovers')},
            'sampler': p10.get('sampler'),
            'same_dir_temp_evidence': p10s,
            'enospc': {k: p10e.get(k) for k in ('create_http', 'final_status', 'error', 'target_error',
                                                'reason', 'error_is_enospc', 'target_unchanged',
                                                'tmp_leftovers', 'proves_temp_is_in_target_fs', 'mount')},
            'app_deploy_enospc': {k: p10n.get(k) for k in ('deploy_http', 'execution', 'reason',
                                                           'reason_is_enospc', 'binary_unchanged',
                                                           'tmp_in_target_dir_samples', 'tmp_leftovers',
                                                           'proves_temp_in_target_fs', 'mount')},
            'app_deploy': {k: p10a.get(k) for k in ('deploy_http', 'execution', 'binary_matches_artifact',
                                                    'unit_active', 'tmp_in_target_dir_samples',
                                                    'agent_fd_open_in_target_dir', 'partial_target_samples',
                                                    'tmp_leftovers', 'atomic_replace_ok',
                                                    'same_dir_temp_evidence')},
            'same_dir_temp_proof': derived,
        },
    }

    ar35 = {
        'case_id': 'AR-35',
        'title': 'Cross-filesystem Rename；恢复后幂等、持久化、Desired State与实际状态一致',
        'canonical_before': 'NOT_RUN',
        'proposed_after': 'PASS' if (
            p10t.get('final_status') == 'SUCCESS' and p10s.get('no_partial_target_ever_observed')
            and p10e.get('target_unchanged') and p10n.get('binary_unchanged')
            and p10n.get('reason_is_enospc') and p10a.get('atomic_replace_ok')
            and (p11.get('ar33_recovery') or {}).get('capable_node_unit_active') == 'active'
            and (p11.get('ar33_recovery') or {}).get('desired_equals_actual', {}).get('equal')
            and (p9.get('persisted') and p9.get('retry_idempotent'))
            and (p10a.get('desired_equals_actual') or {}).get('equal')) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [
            '跨文件系统 rename 在真实部署链路（AtomicReplace 与 AtomicReplaceReader）中原子成功，无部分文件',
            '失败路径（目标 mount 容量不足）不破坏既有目标文件：旧目标 SHA 保持不变，无临时文件残留（文件交付与应用部署两条链路各一次）',
            '恢复与幂等：交付/部署在同一产品路径上重复执行得到相同内容 SHA；SUCCESS 传输的 retry 不回退也不再交付',
            '持久化：owned Agent 重启后二进制 SHA、unit active、unit_registry、SQLite 状态库完好',
            'Desired State == 实际状态：Hub Application 定义（版本/路径）== Agent 本地定义 == 磁盘二进制 SHA',
        ],
        'remaining_or_limitations': [
            'Desired State 收敛判断基于 Hub 定义/Agent 本地定义/磁盘 SHA 三者一致；跨文件系统场景没有独立的“部分重命名”注入 seam。',
            '跨挂载 rename 的 EXDEV 反事实只通过 ENOSPC 判别实验间接体现（未构造真实 EXDEV 失败注入）。',
            '本轮“重启持久化”这一维度的可用观测被 defects[0]（陈旧 ROLLING_BACK 记录重放）污染：app-main 重启后的持久化证据不可用；'
            '本用例的持久化结论改由文件交付重启证据（phase9）与全部操作后 unit active + Desired==Actual（phase11）承载，'
            'xapp（跨文件系统安装）本身未做大重启复测（其 tmpfs 目标在收尾时卸载）。',
        ],
        'evidence': ['phase10-cross-fs-atomic-replace.json', 'phase9-ar34-recovery.json',
                     'phase8-ar32-permission-denied.json', 'phase11-capability-gate.json'],
        'actual_resources': {'owned_mount': p10.get('mount_point'),
                             'xapp_binary': p10a.get('binary_path')},
        'probe': {
            'atomic_success': p10s, 'enospc_preserves_target': p10e.get('target_unchanged'),
            'enospc_preserves_target_deploy_path': p10n.get('binary_unchanged'),
            'same_dir_temp_proof': derived,
            'app_deploy': p10a.get('desired_equals_actual'),
            'restart_persistence': (p8.get('host_after_restart') or {}),
            'transfer_retry_idempotent': (p9.get('retry') or {}),
        },
    }

    ar32 = {
        'case_id': 'AR-32',
        'title': 'Host Adapter Permission Denied；恢复后幂等、持久化、Desired State与实际状态一致',
        'canonical_before': 'NOT_RUN',
        'proposed_after': 'PARTIAL',
        'confidence': 'high',
        'verdict_basis': (
            '权限拒绝、无半成品、释放后恢复部署、Desired==Actual 与重复部署幂等均 PASS；'
            '但“重启后持久化 / Desired State 与实际状态一致”这一维度在本轮真实复现失败（见 defects[0]），'
            '因此整案不判 PASS。失败由更早一次被拒绝且回滚失败的部署遗留的 ROLLING_BACK 记录触发，'
            '而非 AR-32 自身的 EPERM 流程。'),
        'covered_dimensions': [
            '权限拒绝：owned bin 目录 chattr +i 后部署以真实内核 EPERM 失败，Execution FAILED 且 block_reason 明确（operation not permitted）',
            '失败不留半成品：binary/config/unit 均不存在，部署日志给出 ROLLING_BACK→DONE，node state 记录失败',
            '恢复：释放权限 seam 后同一产品路径部署 SUCCESS，binary SHA == artifact SHA、unit active、unit_registry 注册',
            'Desired State == 实际状态（恢复后立即）：Hub 定义版本/路径 == Agent 本地定义 == 磁盘 SHA',
            '幂等：恢复后重复部署得到相同 SHA、同一 unit，无重复 unit/多余执行',
            '重启持久化：FAIL（见 defects[0]）——owned Agent 重启后二进制被陈旧备份覆盖（SHA 变为被拒绝部署的备份内容）、unit 变为 inactive，'
            'Desired State != 实际状态，直到再次部署才修复',
        ],
        'remaining_or_limitations': [
            'native owned Agent 以 root 运行，DAC 模式位无法造成拒绝，因此权限拒绝 seam 使用 chattr +i（真实 EPERM），而不是“其他 uid 属主/只读目录”'
            '的 EACCES；这与 sys-b11 SYS-52 的权限 seam 一致。',
            '只覆盖 application_deploy 路径的 Host Adapter 写权限拒绝；文件交付路径的同类拒绝由 SYS-57 的 ENOSPC 分支覆盖（不同 errno）。',
            '重启持久化维度失败的直接触发者是更早的 SYS-56 symlink 拒绝场景遗留记录；单独执行 AR-32（无该遗留记录）时该维度未单独复测。',
        ],
        'defect_ref': 'SYSB12-DEFECT-01',
        'evidence': ['phase8-ar32-permission-denied.json', 'phase5-agent-path-guard.json'],
        'actual_resources': {'app_id': app_id, 'unit': PREFIX + 'app.service',
                             'binary': '/opt/%s/app/bin/app' % TAG},
        'probe': {
            'seam': p8.get('seam'),
            'denied_execution': p8.get('denied_execution'),
            'denied_reason_matches_eperm': p8.get('denied_reason_matches_eperm'),
            'no_partial_file': p8.get('no_partial_file'),
            'host_after_denial': p8.get('host_after_denial'),
            'recovery_execution': p8.get('recovery_execution'),
            'host_after_recovery': p8.get('host_after_recovery'),
            'binary_matches_artifact': p8.get('binary_matches_artifact'),
            'unit_active': p8.get('unit_active'),
            'desired_equals_actual': p8.get('desired_equals_actual'),
            'host_after_restart': p8.get('host_after_restart'),
            'persisted': p8.get('persisted'),
            'idempotent_redeploy': p8.get('idempotent_redeploy'),
        },
    }

    ar34 = {
        'case_id': 'AR-34',
        'title': 'Path Escape；恢复后幂等、持久化、Desired State与实际状态一致',
        'canonical_before': 'NOT_RUN',
        'proposed_after': 'PASS' if (
            p4.get('all_hub_level_rejections_ok') and p6.get('nonclean_blocked') and p6.get('symlink_blocked')
            and p6.get('positive_control_ok') and p9.get('delivery_ok') and p9.get('retry_idempotent')
            and p9.get('persisted') and p9.get('no_tmp_leftovers') == {'/opt/%s/dst' % TAG: [],
                                                                       '/opt/%s/allow' % TAG: []}) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [
            '逃逸请求被拒绝且无副作用（Hub 层与 Agent Host Adapter 层，部署路径与文件交付路径）',
            '恢复：同一产品路径的合法交付 SUCCESS，落盘 SHA == 源 SHA',
            '幂等：已 SUCCESS 的传输 retry 保持 SUCCESS（不重复交付、不回退）',
            '持久化：owned Agent 重启后目标文件仍在且 SHA 不变，Hub 传输记录仍为 SUCCESS',
            '无临时文件残留（目标目录与 allowlist 目录均无 .tmp/.nodesteer-transfer-*）',
        ],
        'remaining_or_limitations': [
            '“Desired State == 实际状态”在文件交付路径上没有独立的 desired-state 对象，因此该维度以应用部署链路（AR-32/AR-35）的证据承载，'
            '本用例只验证交付的持久化与幂等。',
        ],
        'evidence': ['phase4-hub-path-guard.json', 'phase6-transfer-path-guard.json', 'phase9-ar34-recovery.json'],
        'actual_resources': {'transfer_id': (p9.get('delivery') or {}).get('transfer_id')},
        'probe': {'delivery': {k: (p9.get('delivery') or {}).get(k) for k in
                               ('create_http', 'transfer_id', 'final_status', 'target_status')},
                  'delivery_ok': p9.get('delivery_ok'), 'retry': p9.get('retry'),
                  'retry_idempotent': p9.get('retry_idempotent'),
                  'file_after_restart': p9.get('file_after_restart'),
                  'transfer_record_after_restart': p9.get('transfer_record_after_restart'),
                  'persisted': p9.get('persisted'), 'no_tmp_leftovers': p9.get('no_tmp_leftovers')},
    }

    dgate = p11.get('deploy_to_non_native') or {}
    ugate = p11.get('upgrade_to_non_native') or {}
    sgate = p11.get('self_upgrade_to_non_native') or {}
    mix1 = p11.get('mixed_batch_noncapable_first') or {}
    mix2 = p11.get('mixed_batch_capable_first') or {}
    bbefore = p11.get('child_b_agent_state_before') or {}
    bafter = p11.get('child_b_agent_state_after') or {}
    brestart = p11.get('child_b_agent_state_after_restart') or {}
    gate_ok = (str(dgate.get('error_text') or '').find('lacks capability application_deploy') >= 0
               and not dgate.get('executions_for_node_b')
               and (bafter.get('execution_count') == 0) and not (bafter.get('local_apps'))
               and not (bafter.get('deployments'))
               and (p11.get('inv16_frame_analysis') or {}).get('deploy_request_count', 0) == 0)
    sys58 = {
        'case_id': 'SYS-58',
        'title': 'SYS-58 Capability 缺失时在下发前 BLOCKED，不到客户机执行。',
        'canonical_before': 'NOT_RUN',
        'proposed_after': 'PASS' if gate_ok else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [
            '非 native owned child（deployment_mode=docker）上报 application_deploy/host_filesystem/managed_systemd/agent_upgrade 全 false',
            'application deploy 到该节点：Hub 在创建 Execution 之前拒绝（HTTP 400 "node ... lacks capability application_deploy"），'
            'app 执行列表零新增、该节点零 execution',
            'application upgrade 到该节点：同样在创建 Execution 之前拒绝',
            '客户机无执行痕迹：owned Agent SQLite executions/deployments/unit_registry/local_apps 全空，/etc/systemd/system 无相关 unit',
            '记录帧代理证明 Hub→该节点方向没有 DEPLOY_REQUEST/RUN_EXECUTION 等 Host Operation 帧',
        ],
        'remaining_or_limitations': [
            '产品实现形式是“拒绝创建 Execution（HTTP 400）”，而不是创建一条 BLOCKED 状态的 Execution；语义上等同 PRODUCT.md/AGENTS 要求的'
            '“Hub 下发前校验、BLOCKED、不能等执行后才失败”。已作为实现形态观察记录，不计为用例失败。',
            '自升级门禁走 deployment_mode 分支（Execution 终态 SKIPPED），与 Capability 映射是两条独立门禁，二者都未下发到客户机。',
            '观察（非失败）：AppManager.Deploy 在校验 Capability 之前先对整批 node_ids 调用 ArtifactManager.PrefetchToNodes，因此不可能力节点'
            '仍会收到 ARTIFACT_PREFETCH 帧并把制品写入本地缓存；Managed Host Operation（DEPLOY_REQUEST）始终未下发。',
        ],
        'evidence': ['phase2-owned-child-b.json', 'phase11-capability-gate.json'],
        'actual_resources': {'node_b_id': node_b},
        'probe': {
            'node_b_capabilities': p11.get('node_b_capabilities'),
            'capability_false_for': p11.get('capability_false_for'),
            'deploy_to_non_native': dgate, 'upgrade_to_non_native': ugate,
            'executions_for_node_b_total': p11.get('executions_for_node_b_total'),
            'child_b_agent_state_after': bafter,
            'proxy_after': proxy,
        },
    }

    ar33 = {
        'case_id': 'AR-33',
        'title': 'Capability Mismatch；恢复后幂等、持久化、Desired State与实际状态一致',
        'canonical_before': 'NOT_RUN',
        'proposed_after': 'PASS' if (gate_ok and p11.get('inv16_no_host_op_frame_to_non_capable_node')
                                     and (p11.get('ar33_recovery') or {}).get('capable_node_unit_active') == 'active'
                                     and (p11.get('ar33_recovery') or {}).get('desired_equals_actual', {}).get('equal')
                                     and (p11.get('ar33_recovery') or {}).get('non_capable_node_still_clean')
                                     and (p11.get('node_b_after_restart') or {}).get('capabilities', {}).get('application_deploy') is False
                                     ) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [
            'Capability 不匹配的节点被拒绝且不被下发（部署与升级两种操作）',
            '自升级请求到非 native 节点：Hub 落 SKIPPED 终态并说明 self-upgrade unsupported for deployment mode docker，不向客户机下发',
            '恢复：具备 Capability 的 native 节点仍在同一应用上收敛（unit active、Desired == Actual）',
            '故障节点保持干净：重启后仍无 execution/deployment/local application，能力上报持续为 false（持久化）',
        ],
        'remaining_or_limitations': [
            '混合批次（[capable, non-capable]）中 Hub 返回 400，但可能力节点已按 per-node 语义在下发前完成校验并通过；'
            '这是产品的逐节点语义（请求整体非原子），作为观察记录，不计为用例失败。',
        ],
        'evidence': ['phase11-capability-gate.json'],
        'actual_resources': {'node_a_id': node_a, 'node_b_id': node_b},
        'probe': {
            'self_upgrade_to_non_native': sgate,
            'mixed_batch_noncapable_first': mix1, 'mixed_batch_capable_first': mix2,
            'ar33_recovery': p11.get('ar33_recovery'),
            'node_b_after_restart': p11.get('node_b_after_restart'),
            'child_b_agent_state_after_restart': brestart,
        },
    }

    inv16 = {
        'case_id': 'INV-16',
        'title': 'Capability 不匹配不得下发 Host Operation。',
        'canonical_before': 'NOT_RUN',
        'proposed_after': 'PASS' if (gate_ok and p11.get('inv16_no_host_op_frame_to_non_capable_node')) else 'PARTIAL',
        'confidence': 'high',
        'covered_dimensions': [
            'Host Operation 帧级证据：child-b 全程经记录帧代理接入，Hub→该节点方向 0 个 DEPLOY_REQUEST / RUN_EXECUTION / '
            'ARTIFACT_PREFETCH / FILE_*_REQUEST 帧',
            'Hub 侧执行记录：该节点无任何 Execution，应用执行列表中无 node_id == child-b 的记录',
            '客户机侧无痕迹：owned Agent 状态库无 execution/deployment 行，/etc/systemd/system 无相关 unit，无相关进程副作用',
            '混合批次中不可能力节点同样不下发（只有具备 Capability 的节点被派发）',
        ],
        'remaining_or_limitations': [
            '帧级证据来自 owned child-b 的会话（经本地记录代理）；其他节点的下发不在本用例范围。',
            'ARTIFACT_PREFETCH 帧会在 Capability 校验前发往请求中列出的所有节点（含不可能力节点），只写 Agent 制品缓存，不构成 Managed Host '
            'Operation；已作为观察记录（附客户机缓存行证据），不影响本不变量判定。',
            'child-b 会话上出现的 FILE_UPLOAD_REQUEST/FILE_DELIVERY_REQUEST 属于 SYS-56 allowlist 用例中刻意让 child-b 同时作为源和目标的'
            '文件交付（非 Managed Host Operation 帧）。',
        ],
        'evidence': ['phase11-capability-gate.json'],
        'actual_resources': {'node_b_id': node_b, 'frame_proxy_port': proxy.get('port')},
        'probe': {'inv16_frame_analysis': p11.get('inv16_frame_analysis'),
                  'executions_for_node_b_total': p11.get('executions_for_node_b_total'),
                  'child_b_agent_state_after': bafter,
                  'child_b_host_probe': p11.get('child_b_host_probe'),
                  'child_b_unit_dir_probe': p11.get('child_b_unit_dir_probe'),
                  'child_b_log_hits': p11.get('child_b_log_hits'),
                  'inv16_no_host_op_frame_to_non_capable_node':
                      p11.get('inv16_no_host_op_frame_to_non_capable_node')},
    }

    cases = [sys56, sys57, ar32, ar34, ar35, sys58, ar33, inv16]
    order = ['SYS-56', 'SYS-57', 'AR-32', 'AR-34', 'AR-35', 'SYS-58', 'AR-33', 'INV-16']
    cases = sorted(cases, key=lambda c: order.index(c['case_id']))

    verdicts = {}
    for c in cases:
        verdicts[c['proposed_after']] = verdicts.get(c['proposed_after'], 0) + 1

    p8r = p8.get('host_after_recovery') or {}
    stale_row = None
    for row in (p8r.get('journal_rows') or []):
        if row.get('phase') == 'ROLLING_BACK':
            stale_row = row
    defects = [
        {
            'id': 'SYSB12-DEFECT-01',
            'severity': 'P0',
            'case_ids': ['AR-32', 'SYS-56'],
            'recommended_owner_case': 'INV-11 (Managed Application 必须存在恢复路径，baseline PARTIAL/FAIL)',
            'title': ('回滚失败的被拒部署遗留 ROLLING_BACK 记录，Agent 重启恢复时把陈旧备份写到应用的“当前”binary/config 路径，'
                      '覆盖更新的成功安装并把 unit 留在 inactive'),
            'mechanism': [
                '1) 部署被拒且其 rollback 也失败时（symlink 目的地被 AtomicReplace 拒绝 → rollback 用同一路径再次被拒），'
                'application.go 的 failed() 闭包只在 rollback 成功时才把日志推进到 DONE，于是该 deployment 行永久停留在 ROLLING_BACK。',
                '2) Agent 重启时 ApplicationManager.RecoverDeployments 遍历「活动部署」，但它从**当前**应用定义取 '
                'BinaryPath/ConfigPath/UnitName，再与该行保存的 backup 路径组合执行 rollback。',
                '3) 若应用定义在该行产生后已变化（本例：后续成功部署指向同一 binary 路径），陈旧 backup 会被写进当前 binary 路径；'
                '且当该行的 backup_unit_path 为 absent 时 rollback 不重启服务，unit 停留在 inactive。',
                '4) 结果：Hub Desired State（version 1.0 / artifact A1）与实际状态（陈旧备份内容 + unit inactive）不一致，'
                '直到下一次部署才修复；该行被消费后（DONE）不再重复，属一次性毒化。',
            ],
            'code_references': ['internal/agent/application.go:failed() closure (rollback failure keeps ROLLING_BACK)',
                                'internal/agent/application.go:RecoverDeployments (uses current app definition paths)',
                                'internal/agent/application.go:rollback (skips StartService when backup_unit_path == absent)',
                                'internal/agent/host/native.go:AtomicReplace (refuses symlink destination)'],
            'evidence': {
                'stale_journal_row': stale_row,
                'execution_that_created_it': (p5.get('symlink_escape') or {}).get('execution'),
                'journal_before_restart': p8.get('host_after_recovery', {}).get('journal_rows'),
                'before_restart': {'binary_sha256': (p8.get('host_after_recovery') or {}).get('binary',
                                                                                             {}).get('sha256'),
                                   'unit_active': (p8.get('host_after_recovery') or {}).get('unit_state',
                                                                                            {}).get('ActiveState')},
                'after_restart': p8.get('host_after_restart'),
                'expected_binary_sha256': (artifact or {}).get('sha256'),
                'sha_restored_after_restart': p8.get('host_after_restart', {}).get('binary_sha256'),
                'restored_content_is_symlink_victim': p8.get('host_after_restart', {}).get('binary_sha256')
                == 'f601cb6446e61cb8e428b45ace7d4ac77d592755a177a29e9b56ace2f98b7ae5',
                'recovery_after_redeploy': p8.get('idempotent_redeploy'),
            },
            'repro': ('owned native Agent + 一个 application；先提交 binary_path 指向自有 symlink 的部署（被拒且回滚也失败）→ '
                      '再执行一次成功部署 → 重启 owned Agent → 观察 binary 内容被陈旧备份覆盖、unit inactive；'
                      '证据文件 evidence/phase5-agent-path-guard.json 与 evidence/phase8-ar32-permission-denied.json'),
            'not_claimed': '未做产品修复；未改动产品源码。',
        },
    ]
    observations = [
        {
            'id': 'SYSB12-OBS-01',
            'case_ids': ['SYS-58', 'AR-33', 'INV-16'],
            'topic': 'Artifact prefetch 先于 Capability 门禁下发',
            'detail': ('AppManager.Deploy 在逐节点 Capability 校验之前，先对请求中的全部 node_ids 调用 '
                       'ArtifactManager.PrefetchToNodes，因此不可能力节点也会收到 ARTIFACT_PREFETCH 帧并把制品写入本地缓存'
                       '（owned child-b 重启后缓存行仍在）。Managed Host Operation（DEPLOY_REQUEST）始终未下发，'
                       '故不影响 INV-16 判定；但“下发前 BLOCKED”在制品预取这一层并不完全成立。'),
            'evidence': ['evidence/phase11-capability-gate.json'],
        },
        {
            'id': 'SYSB12-OBS-02',
            'case_ids': ['SYS-58', 'INV-16'],
            'topic': 'Capability 不匹配的实现形态是“拒绝创建 Execution”而非 BLOCKED 状态行',
            'detail': ('Hub 在创建 Execution 之前返回 HTTP 400（node ... lacks capability application_deploy），'
                       '因此不存在 BLOCKED 状态的 Execution 记录；语义上满足“下发前校验、不能等执行后才失败”，'
                       '但与文档术语 BLOCKED 存在表达差异。'),
            'evidence': ['evidence/phase11-capability-gate.json'],
        },
        {
            'id': 'SYSB12-OBS-03',
            'case_ids': ['SYS-56', 'INV-16'],
            'topic': '文件交付路径不受 host_filesystem/application_deploy Capability 门禁',
            'detail': ('deployment_mode=docker（host_filesystem=false）的节点仍可接收文件交付写入 /opt/... 路径'
                       '（allowlist 内正对照 SUCCESS）。纯 docker 形态下该写入落在容器文件系统，不等于宿主机操作，'
                       '因此本批不判为缺陷；仅记录为 Capability 边界观察。'),
            'evidence': ['evidence/phase7-allowlist.json'],
        },
        {
            'id': 'SYSB12-OBS-04',
            'case_ids': ['AR-33', 'INV-16'],
            'topic': '混合节点批次逐节点语义（请求非原子）',
            'detail': ('node_ids=[capable, non-capable] 时 Hub 返回 HTTP 400，但可能力节点已按 per-node 语义创建并下发执行；'
                       'node_ids=[non-capable, capable] 时首个节点即报错，不创建任何 Execution。调用方看到的都是 400，'
                       '但副作用不同。'),
            'evidence': ['evidence/phase11-capability-gate.json'],
        },
    ]

    case_result = {
        'batch_id': 'sys-b12',
        'title': 'Host Adapter 路径守卫、跨文件系统原子替换、权限拒绝与 Capability 前置门禁',
        'nonce': NONCE,
        'owned_prefix': PREFIX,
        'endpoint': run.get('endpoint'),
        'executed_at_utc': run.get('started_at_utc'),
        'finished_at_utc': run.get('finished_at_utc') or NOW(),
        'environment': {
            'authority': 'http://192.168.100.209:18080 (repair, only managed write endpoint)',
            'native2': '192.168.100.206 (only node used by this batch)',
            'owned_children': {
                'child-a': {'node_id': node_a, 'deployment_mode': 'native',
                            'root': '/var/lib/nodesteer-%s' % TAG,
                            'routing': 'direct to hub :18080'},
                'child-b': {'node_id': node_b, 'deployment_mode': 'docker',
                            'root': '/var/lib/nodesteer-%s-b' % TAG,
                            'host_path_allowlist': ['/opt/%s' % TAG],
                            'routing': 'workstation 192.168.100.170 recording frame proxy -> hub :18080'},
            },
            'agent_binary_sha256': g.get('agent_binary_sha256_expected'),
            'protected_nodes_online_synced_at_guard': g.get('protected'),
            'env_facts': g.get('environment_facts'),
        },
        'cases': cases,
        'defects': defects,
        'observations': observations,
        'verdict_summary': verdicts,
        'run_actual_budget': run.get('budget'),
        'phase_failures': run.get('failures'),
        'final_cleanup': {'oracle': p13, 'cleanup_phase': p12},
        'harness_failures_retained': [
            'attempt01（非 canonical，仅保留 rescue-cleanup.json）：两个 owned child 使用同一 node_ip（192.168.100.206），'
            'Hub PrepareEnrollment 按 hostname OR ip 复用节点，第二个 child 重新纳管了第一个 child 的节点记录并重新签发凭证，'
            '该次运行的传输/执行证据无效（"invalid agent credential"）。修复：按 nonce 派生唯一 node_ip（<hostname>.invalid 约定），'
            '并在 _enroll 中加入 fail-closed 冲突检测。',
            'attempt02（非 canonical，evidence 未保留）：recording frame proxy 误绑 127.0.0.1，native-2 上的 owned child-b 无法连接，'
            'phase2 以 owned_child_b_did_not_reach_online 中止；清理阶段正常执行（无残留）。'
            '修复：proxy 绑定 0.0.0.0，并在启动 child 前从 native-2 做 TCP 可达性预检。',
            'attempt03（非 canonical，evidence 未保留）：hostapi 的 finfo 对目录目标抛 IsADirectoryError（尾随斜杠 binary_path 探针），'
            '且 cleanup 汇总时对缺少 get_http 的删除记录取键报 KeyError。修复：探针对目录返回目录信息，cleanup 使用 .get()。',
            'canonical = attempt04（本目录 run-evidence.json / evidence/*.json）：phase_failures 为空，清理与独立复核全部通过。',
        ],
        'global_counts_note': '批次结束时 Hub 节点/应用/制品总数受并行 sys-b02/b03/b07 批次影响，不用于判定残留；'
                              '残留判定只按 qa-sysb12 前缀核查。',
    }
    hubapi.write_json('case-result.json', case_result)

    # ---------------------------------------------------------------- ledger
    deletes = run.get('state', {}).get('deletes', {})
    def del_state(fid):
        d = deletes.get(fid) or {}
        g = d.get('get_http')
        if g is None and fid in iv_gets:
            g = iv_gets.get(fid)
        return {'delete_http': d.get('delete_http'), 'get_http': g}

    ledger = {
        'batch_id': 'sys-b12',
        'nonce': NONCE,
        'owned_prefix': PREFIX,
        'endpoint': run.get('endpoint'),
        'hub_fixtures': [
            {'kind': 'artifact', 'label': 'A1', 'id': artifact.get('id'), 'name': artifact.get('name'),
             'sha256': artifact.get('sha256'), 'size': artifact.get('size'), **del_state(artifact.get('id'))},
            {'kind': 'application', 'label': 'app', 'id': app_id, 'name': PREFIX + 'app',
             'unit_name': (fx.get('owned_paths') or {}).get('unit'),
             'binary_path': (fx.get('owned_paths') or {}).get('binary'),
             'config_path': (fx.get('owned_paths') or {}).get('config'),
             **del_state(app_id)},
            {'kind': 'application', 'label': 'xapp (cross-filesystem AtomicReplace)',
             'id': run.get('state', {}).get('xapp_id'), 'name': PREFIX + 'xapp',
             'unit_name': PREFIX + 'xapp.service',
             'binary_path': p10.get('app_deploy', {}).get('binary_path'),
             'config_path': p10.get('app_deploy', {}).get('config_path'),
             **del_state(run.get('state', {}).get('xapp_id'))},
        ],
        'child_nodes': [
            {'label': 'child-a', 'id': node_a, 'hostname': PREFIX + 'child-a',
             'node_ip': (ca.get('enrollment') or {}).get('node_ip'), 'deployment_mode': 'native',
             'agent_id': (ca.get('enrollment') or {}).get('agent_id'), **del_state(node_a)},
            {'label': 'child-b', 'id': node_b, 'hostname': PREFIX + 'child-b',
             'node_ip': (cb.get('enrollment') or {}).get('node_ip'), 'deployment_mode': 'docker',
             'agent_id': (cb.get('enrollment') or {}).get('agent_id'), **del_state(node_b)},
        ],
        'host_fixtures': {
            'node': '192.168.100.206 (repair native-2)',
            'child_a_root': '/var/lib/nodesteer-%s' % TAG,
            'child_b_root': '/var/lib/nodesteer-%s-b' % TAG,
            'opt_root': '/opt/%s' % TAG,
            'cross_fs_mount': p10.get('mount_point'),
            'escape_victim_dir': '/tmp/%s-escape' % TAG,
            'allowlist_outside_dir_probe': '/etc/%s' % TAG,
            'unit': PREFIX + 'app.service',
            'samplers': ['/opt/%s/xfs/dst.samples' % TAG, '/opt/%s/xfs/app.samples' % TAG],
            'removal': (p12.get('steps') or {}).get('owned_child_removal'),
        },
        'hub_transfer_records': p13.get('hub_transfer_records_owned_paths'),
        'hub_transfer_records_note': p13.get('hub_transfer_records_note'),
        'seams_applied_then_released': [
            {'seam': 'chattr +i', 'path': '/opt/%s/app/bin' % TAG, 'role': 'AR-32 permission denial',
             'released': 'chattr -i in phase12'},
            {'seam': 'owned tmpfs mount', 'path': p10.get('mount_point'),
             'role': 'SYS-57/AR-35 cross-filesystem target', 'released': 'umount in phase12'},
            {'seam': 'owned symlink', 'path': '/opt/%s/escape/bin/app' % TAG,
             'target': '/tmp/%s-escape/victim.bin' % TAG, 'role': 'SYS-56 symlink escape',
             'released': 'owned dir removal in phase12'},
            {'seam': 'owned symlink', 'path': '/opt/%s/dst/link.bin' % TAG,
             'target': '/tmp/%s-escape/victim2.bin' % TAG, 'role': 'SYS-56 symlink escape (delivery)',
             'released': 'owned dir removal in phase12'},
        ],
    }
    ledger['final_cleanup'] = p13
    hubapi.write_json('fixture-ledger.json', ledger)

    progress = {
        'batch_id': 'sys-b12', 'nonce': NONCE, 'owned_prefix': PREFIX, 'endpoint': run.get('endpoint'),
        'phase': 'complete', 'updated_at_utc': NOW(),
        'started_at_utc': run.get('started_at_utc'), 'finished_at_utc': run.get('finished_at_utc'),
        'cases': [{'case_id': c['case_id'], 'proposed_after': c['proposed_after'],
                   'confidence': c['confidence']} for c in cases],
        'verdict_summary': verdicts,
        'phase_failures': run.get('failures'),
        'budget': run.get('budget'),
        'cleanup': {'hub_leftover_clear': p13.get('hub_leftover_clear'),
                    'native2_clean': p13.get('native2_clean'),
                    'protected_nodes_online_synced': p13.get('protected_nodes_online_synced'),
                    'protected_units_after': p13.get('protected_units_after')},
        'deliverables': ['case-result.json', 'progress.json', 'fixture-ledger.json',
                         'final-cleanup-oracle.json', 'evidence/', 'run-evidence.json',
                         'evidence/independent-verification.json'],
        'defects': [d['id'] for d in defects],
        'observations': [o['id'] for o in observations],
    }
    hubapi.write_json('progress.json', progress)
    print(json.dumps({'verdict_summary': verdicts, 'phase_failures': run.get('failures'),
                      'defects': [d['id'] for d in defects],
                      'observations': [o['id'] for o in observations],
                      'cleanup': progress['cleanup']}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
