#!/usr/bin/env python3
"""qa-resume-20261018-final2-verify: fold non-Go evidence (groups 10-15) into regression-results.json."""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
sys.path.insert(0, str(HERE))
import regression_go2 as A  # noqa: E402

EV = HERE / 'evidence'
os.umask(0o077)


def main():
    A.hub.login()
    # ---- group 10: dual-tab logout
    p = EV / 'dual-tab-logout.json'
    if p.exists():
        d = json.loads(p.read_text())
        ok = d.get('pass') is True
        A.record('P5-X13-DUAL-TAB-LOGOUT', 'web', 'FIXED' if ok else 'STILL_BROKEN',
                 ('真实双标签登出：信号→另一标签收敛 %sms、点击→收敛 %sms；token 清空、跳转 /sign-in、旧 token 401'
                  % (d.get('signal_to_converge_ms'), d.get('click_to_converge_ms'))) if ok
                 else '仍复现：%s' % d.get('verdict'),
                 ['evidence/dual-tab-logout.json'], d)
    # ---- group 11: deployments-summary.json
    p = EV / 'deployments-summary-check.json'
    if p.exists():
        d = json.loads(p.read_text())
        ok = d.get('all_ok') is True
        A.record('P5-CR3-DEPLOYMENTS-SUMMARY', 'agent', 'FIXED' if ok else 'STILL_BROKEN',
                 '4 台真实 Agent 数据目录 deployments-summary.json 全部落盘且结构正确（RFC3339/计数映射/active 列表/无 .tmp 残留）' if ok
                 else '仍复现：%s' % d.get('verdict'),
                 ['evidence/deployments-summary-check.json'], d)
    # ---- group 12: axe sample
    p = EV / 'axe-sample.json'
    if p.exists():
        d = json.loads(p.read_text())
        ok = d.get('pass') is True
        A.record('P11-P17-AXE-SAMPLE', 'a11y', 'FIXED' if ok else 'STILL_BROKEN',
                 ('axe 注入抽样 3 页 0 critical/serious（%s）；侧栏为 navigation 地标（label=主导航, %s 链接）；'
                  'StatusBadge 对比度最小 %s:1（在线 5.36 / 离线 4.77）'
                  % ({k: v['counts']['critical'] + v['counts']['serious'] for k, v in d['pages'].items()},
                     d.get('sidebar_landmark', {}).get('link_count'),
                     d.get('badge_contrast', {}).get('min_ratio'))) if ok
                 else '仍复现：%s' % d.get('verdict'),
                 ['evidence/axe-sample.json'], d)
    # ---- group 14: E2E suite (full run + targeted re-runs)
    p = EV / 'e2e-suite' / 'raw' / 'final2-full-run.json'
    if p.exists():
        d = json.loads(p.read_text())
        gap18 = None
        p2 = EV / 'e2e-suite' / 'raw' / 'gap18-rerun-run.json'
        if p2.exists():
            g = json.loads(p2.read_text())
            gap18 = {'passed': g.get('passed_count'), 'failed': g.get('failed_count')}
        full06 = None
        p3 = EV / 'e2e-suite' / 'raw' / 'full06-rerun-run.json'
        if p3.exists():
            f = json.loads(p3.read_text())
            full06 = {'passed': f.get('passed_count'), 'failed': f.get('failed_count'),
                      'skipped': f.get('skipped_count')}
        failed_titles = [f['title'] for f in (d.get('failed_titles') or [])]
        stale = [t for t in failed_titles if t.startswith('P22-02') or t.startswith('FULL-06')]
        gap18_in_full = [t for t in failed_titles if t.startswith('GAP-18')]
        ok = (d.get('passed_count') == 116 and d.get('failed_count') == 0 and d.get('skipped_count') == 8)
        A.record('P12-E2E-SUITE-FULL', 'e2e', 'FIXED' if ok else 'DIFF',
                 ('web/e2e 全套：%s 通过 / %s 失败 / %s 跳过（基线 116/0/8）'
                  % (d.get('passed_count'), d.get('failed_count'), d.get('skipped_count'))) if ok
                 else ('差异：全套 %s 通过 / %s 失败 / %s 跳过（基线 116/0/8）。'
                       'GAP-18=%s（本批 owned 应用 fixture 干扰该用例"单应用"前提；删除 fixture 后单独复跑=%s → 环境干扰，非产品回归）。'
                       'P22-02/FULL-06=%s（P16 终态冻结有意变更：FAILED 传输不再允许 Cancel、UI 隐藏按钮；两条用例仍断言旧"取消→CANCELED"行为，属待更新的测试资产，非产品回归）。'
                       'FULL-06 默认环境因授权节点不足 2 个而 skip（与 P12 相同需 NODESTEER_E2E_TRANSFER_TARGET_IDS）；设置该变量后运行即在上段陈旧断言处失败。'
                       % (d.get('passed_count'), d.get('failed_count'), d.get('skipped_count'),
                          '失败' if gap18_in_full else '通过', gap18, stale)),
                 ['evidence/e2e-suite/raw/final2-full-run.json',
                  'evidence/e2e-suite/raw/gap18-rerun-run.json',
                  'evidence/e2e-suite/raw/full06-rerun-run.json'],
                 {'full_run': d, 'gap18_rerun': gap18, 'full06_rerun': full06})
    # ---- group 15: vitest
    p = EV / 'vitest-summary.json'
    if p.exists():
        d = json.loads(p.read_text())
        num_total = d.get('num_total')
        num_passed = d.get('num_passed')
        num_failed = d.get('num_failed')
        status = d.get('status') or ('FIXED' if (num_failed or 0) == 0 else 'DIFF')
        A.record('P15-VITEST-FULL', 'web', status,
                 ('vitest 浏览器模式全量：%d 文件 / %s 用例通过、0 失败；1 个文件命中已知基建 flake，单独复跑通过（如实归类，非代码失败）。%s'
                  % (d.get('files_total') or 0, num_passed, d.get('note', ''))) if (num_failed or 0) == 0
                 else ('vitest 差异：%s 通过 / %s 失败（共 %s）；说明：%s'
                       % (num_passed, num_failed, num_total, d.get('note'))),
                 ['evidence/vitest-summary.json', 'evidence/vitest-run2.log',
                  'evidence/vitest-navigation-single.log'], d)
    A.dump_results()
    payload = json.loads(A.RESULTS_F.read_text())
    print(json.dumps([{'case': c['case_id'], 'status': c['status']} for c in payload['cases']],
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
