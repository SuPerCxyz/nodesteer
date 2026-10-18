"""汇总修复版新证据；保留原版基线，不用细用例扩大504分母。"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


RUN = Path(__file__).resolve().parent.parent
REPAIR = RUN / 'repair'


def read(name):
    return json.loads((REPAIR / name).read_text())


def write(name, value):
    (REPAIR / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


inventory = read('feature-inventory.json')
cases = {case['case_id']: case for case in inventory['cases']}
review = read('backend-case-crossmap-review.json')
assert len(cases) == review['canonical_denominator'] == 504
assert not review['source_errors'] and not review['old_evidence_reused']

for source in review['cases']:
    if source['status'] == 'NOT_RUN':
        continue
    target = cases[source['case_id']]
    for ref in source['evidence'] + source['binary_sha_evidence']:
        assert (RUN.parents[2] / ref).exists(), ref
    # 不用某一个suite的缺口覆盖其它suite已验证的维度。
    if target['status'] in ('NOT_RUN', 'PARTIAL'):
        target['status'] = source['status']
        target['actual'] = '新修复版真实API/Agent用例已执行，逐维度关联审查见backend-case-crossmap-review.json。'
        target['remaining_dimensions'] = source['remaining_dimensions']
    target['evidence'] = list(dict.fromkeys(target['evidence'] + source['evidence'] + source['binary_sha_evidence']))
    known = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in target['covered_dimensions']}
    for item in source['covered_dimensions']:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in known:
            target['covered_dimensions'].append(item)
            known.add(key)
    target['verified_binary_sha256'] = source['binarySHA']

inventory['summary'] = {status: Counter(case['status'] for case in cases.values())[status]
                        for status in ('PASS', 'FAIL', 'PARTIAL', 'NOT_RUN', 'BLOCKED')}
inventory['updated_at'] = datetime.now(timezone.utc).isoformat()
inventory['complete'] = False
inventory['new_suite_snapshot'] = {
    'backend_real_cases': len(review['verified_suite_results']),
    'review': 'repair/backend-case-crossmap-review.json',
    'note': '细用例数只描述执行量，不增加504 canonical分母。',
}
write('feature-inventory.json', inventory)
print(json.dumps({'repair_case_counts': inventory['summary'],
                  'new_backend_cases': len(review['verified_suite_results'])}, ensure_ascii=False))
