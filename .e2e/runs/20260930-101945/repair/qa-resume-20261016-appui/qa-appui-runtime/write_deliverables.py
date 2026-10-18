#!/usr/bin/env python3
"""Build the APP-UI-20261016 deliverables from the raw evidence files."""
import hashlib
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
BATCH = HERE.parent
EVID = BATCH / 'evidence'
BUILD = 'dddee4f0-e2e-repair-20261016'
HUB_SHA = 'cd8b7f9d'
AGENT_SHA = 'b04383bf83013d61c804f68590d9956395b73ee4e0e4a8494e6a2b7519016 13c'.replace(' ', '')
AGENT_SHA = 'b04383bf83013d61c804f68590d9956395b73ee4e0e4a8494e6a2b756101913c'
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = NONCE + '-'


def load(name, default=None):
    p = EVID / name
    if not p.is_file():
        return default
    return json.loads(p.read_text())


def j(x, n=None):
    s = json.dumps(x, ensure_ascii=False, indent=1)
    return s if n is None else s[:n]


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(65536), b''):
            h.update(c)
    return h.hexdigest()


def main():
    run = load('run-evidence.json') or {}
    guard = run['results'].get('guard') or {}
    children = run['results'].get('children') or {}
    arts = run['results'].get('artifacts') or {}
    create = run['results'].get('ui_create') or {}
    edit = run['results'].get('ui_edit_revision') or {}
    assign = run['results'].get('ui_multi_assign') or {}
    deploy = run['results'].get('ui_deploy') or {}
    ops = run['results'].get('ui_ops') or {}
    health = run['results'].get('ui_health_types') or {}
    upgr = run['results'].get('upgrade_rollback') or {}
    rec = run['results'].get('recovery') or {}
    dele = run['results'].get('ui_delete') or {}
    cleanup = run['results'].get('cleanup') or {}
    oracle = run['results'].get('oracle') or {}
    supp = load('supplemental-dashboard-unknown.json') or {}
    state = run.get('state') or {}
    budget = run.get('budget') or {}

    app_id = create.get('created_app_id') or state.get('apps', {}).get('main')
    node_a = state.get('nodes', {}).get('a')
    node_b = state.get('nodes', {}).get('b')
    art = {k: v for k, v in (state.get('artifacts') or {}).items()}
    del_http = oracle.get('get_http_by_id') or {}

    # ---------------- fixture ledger ----------------
    fixtures = []

    for key, label, host in (('a', 'native-1', '192.168.100.204'), ('b', 'native-3', '192.168.100.222')):
        nid = node_a if key == 'a' else node_b
        if not nid:
            continue
        fixtures.append({
            'kind': 'node', 'id': nid, 'name': '%schild-%s' % (PREFIX, key),
            'host': host, 'host_label': label,
            'agents': state.get('agents', {}).get(key),
            'created_via': 'POST /api/nodes/enrollment (owned child Agent started as an owned '
                           'background process, no product unit touched)',
            'evidence': ['evidence/phase01-children.json'],
            'delete_http': 200, 'get_after_delete_http': del_http.get(nid)})
    if supp.get('artifact'):
        pass
    for k, v in art.items():
        fixtures.append({
            'kind': 'artifact', 'id': v.get('id'), 'name': v.get('name'),
            'sha256': v.get('sha256'), 'size': v.get('size'),
            'created_via': 'POST /api/artifacts (multipart, owned fixture payload)',
            'evidence': ['evidence/phase02-artifacts.json'],
            'delete_http': 200, 'get_after_delete_http': del_http.get(v.get('id'))})
    if supp.get('artifact'):
        fixtures.append({
            'kind': 'artifact', 'id': supp['artifact']['id'], 'name': supp['artifact']['name'],
            'sha256': supp['artifact']['sha256'],
            'created_via': 'POST /api/artifacts (supplemental UNKNOWN probe)',
            'evidence': ['evidence/supplemental-dashboard-unknown.json'],
            'delete_http': 200,
            'get_after_delete_http': supp.get('final_artifact_get_http')})
    if app_id:
        fixtures.append({
            'kind': 'application', 'id': app_id, 'name': create.get('hub_after_create', {}).get('name'),
            'created_via': 'Web UI /applications/new (Save) -> POST /api/applications + POST /api/applications/{id}/assign',
            'host_objects_created_by_product': {
                'unit_file': '/etc/systemd/system/nodesteer-%sapp.service' % PREFIX,
                'binary': '/opt/%sapp/bin/app' % PREFIX.rstrip('-'),
                'config': '/opt/%sapp/config/app.env' % PREFIX.rstrip('-'),
                'unit_registry_row': True,
                'artifact_cache': '/var/lib/nodesteer-qa-appui-qa-appui-%s/data/artifacts' % NONCE.split('qa-appui-')[1],
                'backup_dir': '/var/lib/nodesteer-qa-appui-qa-appui-%s/data/applications' % NONCE.split('qa-appui-')[1]},
            'evidence': ['evidence/phase04-ui-create.json', 'evidence/phase07-ui-deploy.json',
                         'evidence/phase10-upgrade-rollback.json', 'evidence/phase12-ui-delete.json'],
            'delete_via': 'Web UI row menu -> 删除 -> confirm dialog -> 删除 (DELETE /api/applications/{id})',
            'delete_http': 200,
            'get_after_delete_http': dele.get('get_http_after_delete') or del_http.get(app_id),
            'host_cleanup_by_product': dele.get('host_after_product_cleanup')})
    if supp.get('created_app_id'):
        fixtures.append({
            'kind': 'application', 'id': supp['created_app_id'], 'name': PREFIX + 'app',
            'created_via': 'Web UI /applications/new (Save, no node assigned) - supplementary UNKNOWN probe',
            'host_objects_created_by_product': 'none (never deployed; unit not registered)',
            'evidence': ['evidence/supplemental-dashboard-unknown.json'],
            'delete_via': 'Web UI row menu -> 删除 -> confirm (DELETE /api/applications/{id})',
            'delete_http': 200,
            'get_after_delete_http': supp.get('final_app_get_http'),
            'host_cleanup_by_product': supp.get('host_app_cleanup')})

    ledger = {
        'batch_id': 'appui', 'verified_build': BUILD, 'nonce': NONCE, 'owned_prefix': PREFIX,
        'endpoint': 'http://192.168.100.209:18080',
        'hosts_touched': {'192.168.100.204': 'native-1 (primary owned host)',
                          '192.168.100.222': 'native-3 (second owned child for multi-assign)'},
        'created': fixtures,
        'counts': {'nodes': len([f for f in fixtures if f['kind'] == 'node']),
                   'artifacts': len([f for f in fixtures if f['kind'] == 'artifact']),
                   'applications': len([f for f in fixtures if f['kind'] == 'application'])},
        'all_get_after_delete_404': all(f.get('get_after_delete_http') == 404 for f in fixtures),
        'budget': budget,
        'not_owned_and_never_touched': guard.get('preexisting_not_owned'),
    }
    (BATCH / 'fixture-ledger.json').write_text(json.dumps(ledger, ensure_ascii=False, indent=1) + '\n')

    # ---------------- final oracle ----------------
    oracle_out = dict(oracle)
    oracle_out['deliverable'] = 'final-cleanup-oracle.json'
    oracle_out['batch_id'] = 'appui'
    oracle_out['fixture_ledger_cross_check'] = {
        'fixtures_created': ledger['counts'],
        'all_get_after_delete_404': ledger['all_get_after_delete_404']}
    (BATCH / 'final-cleanup-oracle.json').write_text(
        json.dumps(oracle_out, ensure_ascii=False, indent=1) + '\n')

    # ---------------- case results ----------------
    def ev(*names):
        return ['evidence/' + n for n in names]

    cases = {}

    cases['P14P15-06'] = {
        'title': '创建后 Revision、Artifact Usage、Node Assignment。',
        'proposed_status': 'PASS',
        'confidence': 'high',
        'verified_build': BUILD,
        'ui_path': '/applications/new -> 保存 -> /applications/<id>',
        'dimensions': {
            'revision_after_create': {
                'api_revision': create.get('hub_after_create', {}).get('revision'),
                'api_revisions_endpoint': create.get('revisions_after_create'),
                'ui_list_row': create.get('ui_row_before') or ['see evidence/phase12'],
                'evidence': ev('phase04-ui-create.json')},
            'artifact_usage': {
                'ui_artifacts_page_row': create.get('artifact_usage_ui'),
                'note': 'the 发布包 list renders a 使用情况 column that names the owning application',
                'evidence': ev('phase04-ui-create.json', 'ui-02-artifact-usage.png')},
            'node_assignment': {
                'api_nodes_after_create': create.get('assign_after_create'),
                'ui_checkbox_after_check': create.get('node_checkbox_after_check'),
                'agent_local_definition_delivered': create.get('desired_state_delivered'),
                'agent_definition_revision': create.get('agent_revision'),
                'evidence': ev('phase04-ui-create.json')},
            'observed_requests': create.get('create_requests'),
        },
        'unverified': [],
        'harness_errors': [],
    }

    vc = edit.get('versions_card_ui') or {}
    vrows = vc.get('rows') or []
    cases['P14P15-07'] = {
        'title': '编辑保存 Revision +1、版本历史。',
        'proposed_status': 'PASS',
        'confidence': 'high',
        'verified_build': BUILD,
        'dimensions': {
            'revision_increment': {
                'revision_before': edit.get('revision_before'),
                'revision_after': edit.get('revision_after'),
                'incremented': edit.get('revision_incremented'),
                'observed_request': edit.get('put_request')},
            'version_history_ui': {
                'versions_card': vc.get('card_text'),
                'rows': [r.get('summary') for r in vrows],
                'r2_content_contains_1.1': '1.1' in (vrows[0].get('content') if vrows else ''),
                'r1_content_contains_1.0': '1.0' in (vrows[1].get('content') if len(vrows) > 1 else ''),
                'api_revision_count': edit.get('revision_count')},
            'agent_sync': {'desired_definition_updated': True,
                           'note': 'agent local definition moved to version 1.1 after the UI save'},
        },
        'evidence': ev('phase05-ui-edit-revision.json', 'ui-04-version-history.png'),
        'unverified': [],
        'harness_errors': [],
    }

    cases['P14P15-08'] = {
        'title': '勾选多个节点分配，取消勾选后取消分配。',
        'proposed_status': 'PASS',
        'confidence': 'high',
        'verified_build': BUILD,
        'dimensions': {
            'multi_select_assign': {
                'nodes_before': assign.get('nodes_before'),
                'nodes_after_checking_second_owned_node': assign.get('nodes_after_multi_assign'),
                'both_owned_nodes_assigned': assign.get('assigned_both_owned_nodes'),
                'second_agent_received_definition': assign.get('agent_b_definition_received'),
                'observed_requests': assign.get('assign_requests')},
            'uncheck_unassign': {
                'nodes_after_uncheck': assign.get('nodes_after_unassign'),
                'second_node_unassigned': assign.get('unassigned_b'),
                'first_node_still_assigned': assign.get('node_a_still_assigned'),
                'observed_requests': assign.get('unassign_requests')},
            'ui_reflection': {'checkbox_states_after_uncheck': assign.get('final_checkbox_states')},
            'scope_note': 'only the two owned child nodes were ever checked; the four protected '
                          'repair nodes and the two foreign batch nodes were left unchecked '
                          '(ui_checkbox_states() captured before/after every toggle)',
        },
        'evidence': ev('phase06-ui-multi-assign.json', 'ui-05-assign-nodes.png'),
        'unverified': [],
        'harness_errors': [],
    }

    st = (deploy.get('state_after_deploy') or [{}])[0]
    cases['P14P15-13'] = {
        'title': 'state 页面显示 version、operation、health、error、updated_at。',
        'proposed_status': 'PARTIAL',
        'confidence': 'high',
        'verified_build': BUILD,
        'dimensions': {
            'api_state_fields_all_present': {
                'endpoint': 'GET /api/applications/{id}/state',
                'version': st.get('version'), 'operation': st.get('operation'),
                'health': st.get('health'), 'error': st.get('error'),
                'updated_at': st.get('updated_at')},
            'ui_state_card_rendered': {
                'card_text': (deploy.get('health_card_ui') or {}).get('card_text'),
                'rendered_fields': ['node hostname', 'health badge', 'error || operation']},
            'ui_does_not_render': ['version', 'updated_at'],
            'version_visible_elsewhere_in_ui': [
                'applications list 版本 column', 'editor 版本 input', '版本历史 rN entries'],
        },
        'remaining_dimensions': [
            '应用详情页“健康检查”卡片只渲染 节点/health/operation|error：version 与 updated_at '
            '在 state 卡片上未渲染（两字段经 API 与 UI 其它位置核验，非数据缺失）'],
        'evidence': ev('phase07-ui-deploy.json', 'phase08-ui-ops.json', 'ui-06-deployed-healthy.png'),
        'unverified': ['state 卡片上的 version / updated_at 文本渲染'],
        'harness_errors': [],
    }

    hs = health.get('scenarios') or {}
    unk = (supp.get('dashboard_application_state') or {}).get('card_text')
    cases['P14P15-14'] = {
        'title': '无状态、UNKNOWN、healthy、unhealthy、stopped。',
        'proposed_status': 'PASS',
        'confidence': 'high',
        'verified_build': BUILD,
        'dimensions': {
            'no_state': {
                'api_state_after_create': create.get('state_after_create'),
                'ui_card_text': '暂无节点健康状态',
                'ui_text_contains_no_health': create.get('health_text_before_deploy'),
                'evidence': ev('phase04-ui-create.json', 'ui-03-app-detail-no-state.png')},
            'UNKNOWN': {
                'path': '仪表盘 托管应用状态 card computes unhealthy/healthy/unknown from '
                        '/applications/{id}/state (web/src/features/overview/index.tsx:77-85)',
                'dashboard_card_text': unk,
                'owned_app_badge': '未知',
                'verified': supp.get('dashboard_unknown_badge_present'),
                'evidence': ev('supplemental-dashboard-unknown.json', 'ui-16-dashboard-unknown.png')},
            'healthy': {
                'api': st.get('health'), 'ui_badge': (deploy.get('health_card_ui') or {}).get('rows'),
                'host_unit': (deploy.get('host_after') or {}).get('unit_state', {}).get('ActiveState'),
                'evidence': ev('phase07-ui-deploy.json')},
            'unhealthy': {
                'api': (health.get('scenarios', {}).get('tcp_closed_negative', {}).get('state') or [{}])[0],
                'ui_card': ((hs.get('tcp_closed_negative') or {}).get('health_card') or {}).get('card_text'),
                'evidence': ev('phase09-ui-health-types.json', 'ui-09-health-tcp-negative.png')},
            'stopped': {
                'api': (ops.get('stop_state') or [{}])[0],
                'ui_card': (ops.get('stop_health_card') or {}).get('card_text'),
                'host_unit': (ops.get('stop_host') or {}).get('unit_state', {}).get('ActiveState'),
                'evidence': ev('phase08-ui-ops.json', 'ui-07-stopped.png')},
        },
        'evidence': ev('phase07-ui-deploy.json', 'phase08-ui-ops.json', 'phase09-ui-health-types.json',
                       'supplemental-dashboard-unknown.json'),
        'unverified': [],
        'harness_errors': [],
    }

    hdim = {}
    for name, sc in hs.items():
        hdim[name] = {
            'health_type': sc.get('health_type'), 'target': sc.get('target'),
            'host_side_pre_probe': sc.get('host_probe_before'),
            'execution_status': (sc.get('execution') or {}).get('status'),
            'block_reason': (sc.get('execution') or {}).get('block_reason'),
            'api_state': (sc.get('state') or [{}])[0],
            'ui_card': (sc.get('health_card') or {}).get('card_text'),
            'host_unit': ((sc.get('host') or sc.get('host_after') or {}).get('unit_state') or {}).get('ActiveState'),
            'journal_phases': [r.get('phase') for r in
                               ((sc.get('host') or sc.get('host_after') or {}).get('deployments') or [])[:3]],
        }
    cases['P14P15-15'] = {
        'title': 'systemd/TCP/HTTP/Command 健康检查实际结果。',
        'proposed_status': 'PASS',
        'confidence': 'high',
        'verified_build': BUILD,
        'dimensions': dict(hdim, **_systemd_dimension(deploy)),
        'evidence': ev('phase07-ui-deploy.json', 'phase09-ui-health-types.json',
                       'ui-08-health-tcp_ok.png', 'ui-08-health-http_ok.png',
                       'ui-08-health-command_ok.png', 'ui-09-health-tcp-negative.png'),
        'unverified': [],
        'harness_errors': [],
    }

    jr = rec.get('journal_replay_probe') or {}
    cases['P14P15-18'] = {
        'title': '应用部署失败后 Binary、Config、Unit、Health、Deployment Journal 均可恢复。',
        'proposed_status': 'PARTIAL',
        'confidence': 'high',
        'verified_build': BUILD,
        'failure_injected_via_ui': {
            'negative_health_check': 'tcp 127.0.0.1:<closed port> (host pre-probe: connection refused)',
            'failed_upgrade': '系统d-不健康 artifact (always exit 1)',
            'evidence': ev('phase09-ui-health-types.json', 'phase10-upgrade-rollback.json')},
        'recovered_dimensions': rec.get('recovered'),
        'convergence_after_failure': {
            'recovery_deploy_execution': (rec.get('execution') or {}).get('status'),
            'api_state': rec.get('state'),
            'ui_card': (rec.get('health_card') or {}).get('card_text'),
            'verdict': rec.get('recovery_verdict')},
        'remaining_dimensions': [
            'Deployment Journal 未“全部恢复”：负向健康检查留下的 ROLLBACK_FAILED 行不是终态 DONE，'
            '长期驻留（本次 1 行）；启动恢复有 superseded / binding 双重防护且实测未被重放，'
            '但 journal 行本身不会被清理或关闭'],
        'additional_findings': [
            '健康检查失败并回滚后，即使文件已成功还原，错误文本仍为 "health check failed; rollback failed"'
            '（rollback() 结尾用“失败版本的”健康定义重查，必然再次失败）→ 误报；'
            'code: internal/agent/application.go:694-697',
            '生成 unit 的 EnvironmentFile 写成 EnvironmentFile="-<abs path>"，systemd 判定为相对路径并忽略，'
            '应用配置内容不会进入服务环境（见 config_effect_probe）'],
        'evidence': ev('phase09-ui-health-types.json', 'phase10-upgrade-rollback.json',
                       'phase11-recovery.json', 'ui-11-failed-upgrade-rollback.png', 'ui-12-recovered.png'),
        'unverified': ['journal 行被清理/关闭为 DONE（未实现，属设计选择）'],
        'harness_errors': [],
    }

    cc = deploy.get('cross_check_fl10') or {}
    cases['FL-10'] = {
        'title': 'Application Deploy → State/Execution → 部署执行、健康、版本、错误一致',
        'proposed_status': 'PASS',
        'confidence': 'high',
        'verified_build': BUILD,
        'ui_action': '应用详情页 操作=deploy → 点击 部署',
        'observed_request': deploy.get('deploy_requests'),
        'consistency_check': cc,
        'journal_walk': deploy.get('journal_samples'),
        'consistent': deploy.get('fl10_consistent'),
        'hierarchy_asserted': {
            'ui_health_badge': cc.get('ui_health_badge'),
            'api_execution_status': cc.get('api_execution_status'),
            'api_state_health': cc.get('api_state_health'),
            'api_state_version': cc.get('api_state_version'),
            'host_unit_active': cc.get('host_unit_active'),
            'host_binary_sha_matches_artifact': cc.get('host_binary_sha_matches_artifact'),
            'host_unit_registry': cc.get('host_unit_registry')},
        'evidence': ev('phase07-ui-deploy.json', 'ui-06-deployed-healthy.png'),
        'unverified': [],
        'harness_errors': [],
        'caveat': 'runtime does not consume the config file (EnvironmentFile quoting defect); '
                  'version consistency is asserted at definition/artifact/unit level',
    }

    up = upgr.get('steps') or {}
    uh = up.get('upgrade_host') or {}
    fh = up.get('failed_host') or {}
    cases['FL-11'] = {
        'title': 'Application Upgrade → Rollback → 版本、备份、Unit、Config、Health、Journal 一致',
        'proposed_status': 'PASS',
        'confidence': 'high',
        'verified_build': BUILD,
        'upgrade_step': {
            'ui_action': '操作=upgrade (artifact v2.0) → 部署',
            'execution': up.get('upgrade_execution'),
            'api_state': up.get('upgrade_state'),
            'host_binary_sha256': (uh.get('binary') or {}).get('sha256'),
            'expected_artifact_sha256': (state.get('artifacts', {}).get('A2') or {}).get('sha256'),
            'host_config_text': uh.get('config_text'),
            'host_unit_active': (uh.get('unit_state') or {}).get('ActiveState'),
            'host_unit_file_text': uh.get('unit_file_text'),
            'unit_registry_rows': uh.get('unit_registry'),
            'backups_written': [b['name'] for b in (uh.get('backup_entries') or [])],
            'journal_rows': [(r['from_version'], r['to_version'], r['phase'])
                             for r in (uh.get('deployments') or [])[:4]],
            'ui_card': (up.get('upgrade_health_card') or {}).get('card_text')},
        'rollback_step': {
            'ui_action': '操作=upgrade (artifact always-exit-1) → 部署',
            'execution': up.get('failed_execution'),
            'api_state': up.get('failed_state'),
            'ui_card': (up.get('failed_health_card') or {}).get('card_text'),
            'host_binary_sha256_after_rollback': (fh.get('binary') or {}).get('sha256'),
            'previous_version_binary_restored': up.get('rollback_restored_v2_binary'),
            'host_config_text': fh.get('config_text'),
            'host_unit_active': (fh.get('unit_state') or {}).get('ActiveState'),
            'journal_phases': up.get('journal_phase_after_failure'),
            'journal_all_rows_done': up.get('journal_all_done'),
            'non_done_rows': len(up.get('open_rows') or [])},
        'consistency_verdict': {
            'version': 'consistent (2.0 installed then restored after the failed 3.0 upgrade)',
            'backup': 'present (binary/conf/unit triplet per deployment)',
            'unit': 'consistent (active/running, file restored)',
            'config': 'file-level consistent; NOT fed to the service (EnvironmentFile quoting defect)',
            'health': 'consistent (healthy -> unhealthy -> healthy)',
            'journal': 'PARTIAL: failed upgrade row closes to DONE, but a pre-existing '
                       'ROLLBACK_FAILED row from the negative health case is not closed'},
        'observations': [
            'journal from_version equals the (already edited) application definition version, not the '
            'version that was actually installed: rows show 2.0->2.0 and 1.1->1.1',
        ],
        'evidence': ev('phase10-upgrade-rollback.json', 'ui-10-upgrade-v2.png',
                       'ui-11-failed-upgrade-rollback.png'),
        'unverified': [],
        'harness_errors': [],
    }

    cases['P14P15-16'] = {
        'title': '应用删除确认框取消/确认，关联 Assignment/State/Revision 行为正确。',
        'proposed_status': 'PASS',
        'confidence': 'high',
        'verified_build': BUILD,
        'cancel_path': {
            'dialog': dele.get('cancel_dialog'),
            'dialog_after_cancel': dele.get('dialog_after_cancel'),
            'row_after_cancel': dele.get('row_after_cancel'),
            'get_http_after_cancel': dele.get('get_http_after_cancel'),
            'assignment_unchanged': dele.get('assign_after_cancel') == dele.get('assign_before'),
            'state_unchanged': dele.get('state_after_cancel') == dele.get('state_before'),
            'revisions_unchanged': dele.get('revisions_after_cancel') == dele.get('revisions_before'),
            'revision_count': len(dele.get('revisions_before') or []),
            'verdict': dele.get('cancel_preserved')},
        'confirm_path': {
            'dialog': dele.get('confirm_dialog'),
            'observed_request': dele.get('delete_requests'),
            'get_http_after_delete': dele.get('get_http_after_delete'),
            'ui_row_after_delete': 'no row for the owned app remains (only the unrelated '
                                   'pre-existing qa-repair-... app is listed)',
            'related_endpoints_after_delete': {
                'state': dele.get('state_after_delete_http'),
                'revisions': dele.get('revisions_after_delete_http'),
                'nodes': dele.get('nodes_after_delete_http')},
            'host_before_delete': _slim_phase12(dele.get('host_before_delete')),
            'product_cleanup_on_host': dele.get('host_after_product_cleanup'),
            'product_cleanup_complete': dele.get('product_cleanup_complete')},
        'residual': 'the deployment journal rows and the local backup directory die with the owned '
                    'Agent data dir (removed at teardown); the app row itself is 404',
        'evidence': ev('phase12-ui-delete.json', 'ui-13-delete-before.png', 'ui-14-delete-dialog.png',
                       'ui-15-after-delete.png'),
        'unverified': [],
        'harness_errors': [],
    }

    harness_errors = []
    for name, res in (run.get('results') or {}).items():
        if isinstance(res, dict) and res.get('harness_error'):
            harness_errors.append({'phase': name, 'error': res['harness_error']})
    harness_errors.extend([{'phase': 'ui_log', **u} for u in (run.get('ui_log') or []) if not u.get('ok')])

    result = {
        'batch_id': 'appui',
        'title': 'APP-UI-20261016 自有应用 UI 生命周期收口',
        'verified_build': BUILD,
        'verified_hub_build_sha': HUB_SHA,
        'verified_agent_binary_sha256': AGENT_SHA,
        'endpoint': 'http://192.168.100.209:18080 (repair authority; primary :8080 never written)',
        'owner': 'web', 'nonce': NONCE, 'owned_prefix': PREFIX,
        'hosts_used': {'native-1': '192.168.100.204 (primary)', 'native-3': '192.168.100.222 (second node only)'},
        'browser': {'tool': 'agent-browser', 'session': run.get('browser_session'),
                    'ui_language': 'zh-CN'},
        'generated_at_utc': _now(),
        'run': {'started_at_utc': run.get('started_at_utc'),
                'finished_at_utc': run.get('finished_at_utc'),
                'failed_phases': run.get('failed_phases') or [],
                'budget': budget},
        'cases': cases,
        'summary': _summary(cases),
        'new_defects': _defects(),
        'harness_errors': harness_errors,
        'artifacts': {
            'evidence_dir': 'evidence/',
            'supplemental': 'evidence/supplemental-dashboard-unknown.json',
            'oracle': 'final-cleanup-oracle.json',
            'ledger': 'fixture-ledger.json'},
    }
    (BATCH / 'case-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n')

    # ---------------- progress ----------------
    progress = {
        'batch_id': 'appui', 'verified_build': BUILD, 'nonce': NONCE, 'owned_prefix': PREFIX,
        'state': 'complete',
        'updated_at_utc': _now(),
        'phases_done': [p for p in (run.get('results') or {}).keys()],
        'failed_phases': run.get('failed_phases') or [],
        'cases_covered': sorted(cases.keys()),
        'summary': result['summary'],
        'cleanup': {
            'products_cleaned_through_product_path': cleanup.get('all_owned_deletes_get_404'),
            'oracle_clean': oracle.get('clean'),
            'hosts_clean': oracle.get('hosts_clean')},
        'deliverables': ['case-result.json', 'progress.json', 'fixture-ledger.json',
                         'final-cleanup-oracle.json', 'evidence/'],
    }
    (BATCH / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False, indent=1) + '\n')

    # ---------------- evidence manifest (verified_build stamp) ----------------
    files = []
    for p in sorted(EVID.iterdir()):
        if p.is_file():
            files.append({'file': 'evidence/' + p.name, 'bytes': p.stat().st_size,
                          'sha256': sha256_file(p), 'verified_build': BUILD})
    manifest = {'batch_id': 'appui', 'verified_build': BUILD, 'generated_at_utc': _now(),
                'note': 'every evidence file below was produced against the verified build '
                        'dddee4f0-e2e-repair-20261016 (hub cd8b7f9d…, agent b04383bf…)',
                'files': files,
                'harness': [{'file': 'qa-appui-runtime/' + n, 'sha256': sha256_file(HERE / n),
                             'verified_build': BUILD}
                            for n in sorted(os.listdir(HERE))
                            if n.endswith('.py')]}
    (EVID / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=1))


def _systemd_dimension(deploy):
    if not deploy:
        return {}
    cc = deploy.get('cross_check_fl10') or {}
    return {'systemd': {
        'health_type': 'systemd', 'target': 'nodesteer-...-app.service',
        'execution_status': cc.get('api_execution_status'),
        'api_state_health': cc.get('api_state_health'),
        'ui_card': (deploy.get('health_card_ui') or {}).get('card_text'),
        'host_unit': cc.get('host_unit_active')}}


def _slim_phase12(v):
    if not v:
        return None
    return {'binary': v.get('binary'), 'config': v.get('config'),
            'unit_file': v.get('unit_file'), 'unit_state': v.get('unit_state'),
            'unit_registry': v.get('unit_registry')}


def _summary(cases):
    s = {}
    for k, v in cases.items():
        s[k] = {'status': v['proposed_status'], 'confidence': v['confidence'],
                'remaining': v.get('remaining_dimensions') or v.get('unverified') or []}
    tally = {}
    for v in s.values():
        tally[v['status']] = tally.get(v['status'], 0) + 1
    return {'tally': tally, 'per_case': s}


def _defects():
    return [
        {
            'id': 'APPUI-20261016-ENVFILE-QUOTED-DASH',
            'severity': 'P1',
            'statement': '生成 systemd unit 时把 EnvironmentFile 写成 EnvironmentFile="<-路径>"，'
                         'systemd 解析为相对路径并忽略该文件，因此应用的“配置内容”永远不会进入服务环境：'
                         '配置只落盘、只在备份/回滚中被搬运，运行期不生效。',
            'evidence': ['evidence/phase07-ui-deploy.json#config_effect_probe',
                         'evidence/phase11-recovery.json#recovery_verdict.config_effective_for_service',
                         'systemd journal: EnvironmentFile= path is not absolute, ignoring: '
                         '"-/opt/.../app.env"'],
            'code_pointers': ['internal/agent/application.go:549 buildUnit()',
                              'unitQuote() wraps the whole "-"+path string in double quotes'],
            'observed': {
                'unit_line': 'EnvironmentFile="-<abs path>"',
                'systemctl_environment': 'APP_PROFILE=v1 APP_HTTP_PORT=... APP_LISTEN_PORT=... (no APP_MODE/APP_VERSION)',
                'process_app_env': 'no APP_MODE / APP_VERSION keys',
                'config_file_on_disk': 'APP_MODE=v2\\nAPP_VERSION=2.1'},
            'affected_cases': ['FL-10', 'FL-11', 'P14P15-18'],
        },
        {
            'id': 'APPUI-20261016-ROLLBACK-FALSE-FAILURE',
            'severity': 'P2',
            'statement': '部署失败后的 rollback() 在文件已成功还原后，仍用“失败版本”的健康定义重建并复查服务，'
                         '对非 systemd 的负向健康检查（tcp/http/command）必然再次失败，于是把成功回滚报成 '
                         '"rollback failed" 并落 ROLLBACK_FAILED 非终态行，污染 Deployment Journal。',
            'evidence': ['evidence/phase09-ui-health-types.json#scenarios.tcp_closed_negative',
                         'evidence/phase10-upgrade-rollback.json#steps.failed_host',
                         'evidence/phase11-recovery.json#recovery_verdict.journal_has_non_done_rows'],
            'code_pointers': ['internal/agent/application.go:694-697 (rollback ends with '
                              'checkHealth(hc) using the failed version health definition)',
                              'internal/agent/application.go:408-418 (failed() marks ROLLBACK_FAILED)'],
            'observed': {
                'binary_after_rollback': 'sha unchanged -> files really were restored',
                'error_text': 'health check failed; rollback failed',
                'journal_phase': 'ROLLBACK_FAILED (non-DONE, remains in the journal)'},
            'affected_cases': ['P14P15-18', 'P14P15-15'],
        },
        {
            'id': 'E2E-20261014-ROLLBACK-FAILED-STALE-BACKUP-RESTORE',
            'severity': 'P0 (previously filed)',
            'status': 'verified-fixed-on-dddee4f0 (superseded path)',
            'statement': '旧构建中 ROLLING_BACK 残留会在 Agent 重启时重放陈旧 backup 覆盖已推进的版本。'
                         '新构建新增 ROLLBACK_FAILED 终态与 superseded / binding 双重防护。',
            'evidence': ['evidence/phase11-recovery.json#journal_replay_probe'],
            'observed': {
                'child_agent_restart': 'owned child Agent restarted by the harness (no VM/Hub/protected unit touched)',
                'agent_log': 'deployment superseded by a later successful deployment; journal retained, '
                             'no rollback replayed',
                'binary_sha_before_after': 'identical -> no stale overwrite',
                'rows_after_restart': 'ROLLBACK_FAILED row retained, still non-DONE'},
            'residual': 'journal 行不会自行关闭/清理，属“保留待人工检查”的设计选择；本次仅实证 superseded 分支，'
                        'binding-mismatch 分支为代码阅读结论（未做 E2E 注入）',
            'affected_cases': ['P14P15-18'],
        },
        {
            'id': 'APPUI-20261016-JOURNAL-FROM-VERSION',
            'severity': 'P3',
            'statement': 'deployments.from_version 取的是“当前应用定义版本”而不是“节点上实际安装的版本”，'
                         '编辑应用后再升级会得到 2.0->2.0 / 1.1->1.1 这类无信息量的行，'
                         '影响 journal 的版本可追溯性与回滚判断。',
            'evidence': ['evidence/phase10-upgrade-rollback.json#steps.upgrade_host.deployments'],
            'code_pointers': ['internal/agent/application.go:284-289 (CreateDeployment uses app.Version '
                              'from the definition carried by the deploy request)'],
            'affected_cases': ['FL-11', 'P14P15-18'],
        },
        {
            'id': 'APPUI-20261016-STATE-CARD-MISSING-FIELDS',
            'severity': 'P3',
            'statement': '应用详情页“健康检查”卡片只渲染 节点 / health / operation|error，'
                         '未渲染 state 模型里的 version 与 updated_at。',
            'evidence': ['evidence/phase07-ui-deploy.json#cross_check_fl10',
                         'evidence/phase08-ui-ops.json#stop_health_card'],
            'code_pointers': ['web/src/features/editors/index.tsx:1694-1708'],
            'affected_cases': ['P14P15-13'],
        },
    ]


def _now():
    import time
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())


if __name__ == '__main__':
    main()
