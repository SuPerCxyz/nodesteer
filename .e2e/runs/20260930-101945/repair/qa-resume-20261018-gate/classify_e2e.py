#!/usr/bin/env python3
"""qa-resume-20261018-gate: build e2e-suite.json from the full run + reruns.

Classification vocabulary: PRODUCT_DEFECT / HARNESS_ENV / REDLINE_LIMITED /
SUITE_ASSET (stale assertion). Rerun evidence is attached per failed test.
"""
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
RAW = HERE / 'raw'

full = json.loads((RAW / 'gate-full-summary.json').read_text())

# collected rerun summaries: raw/gate-rerun*-summary.json
reruns = []
for p in sorted(RAW.glob('gate-rerun*-summary.json')):
    reruns.append(json.loads(p.read_text()))

rerun_index = {}
for r in reruns:
    for t in r.get('failed_titles', []) + [{'title': s['title'], 'status': 'skipped'} for s in r.get('skipped_titles', [])] + [{'title': t['title'], 'status': 'passed'} for t in r.get('passed_titles', [])]:
        rerun_index.setdefault(t['title'], []).append({'label': r.get('label'), 'status': t.get('status')})

# classification overrides can be supplied as JSON on the command line
overrides = {}
if len(sys.argv) > 1:
    overrides = json.loads(pathlib.Path(sys.argv[1]).read_text())

failed = []
for t in full.get('failed_titles', []):
    item = {'file': t['file'], 'title': t['title'], 'statuses': t['statuses'],
            'reruns': rerun_index.get(t['title'], []),
            'classification': overrides.get(t['title'], 'PENDING')}
    failed.append(item)

skipped = []
for t in full.get('skipped_titles', []):
    annotations = [a.get('description') for a in t.get('annotations', []) if a.get('type') == 'skip' and a.get('description')]
    skipped.append({'file': t['file'], 'title': t['title'], 'reason': annotations[0] if annotations else None,
                    'classification': 'REDLINE_LIMITED' if annotations else 'PENDING_REASON'})

out = {
    'suite': 'web/e2e (Playwright chromium, workers=1, retries=0, first-run truth)',
    'base_url': 'http://192.168.100.209:18080',
    'build': 'dddee4f0-e2e-repair-20261018-final2',
    'total': (full.get('passed_count') or 0) + (full.get('failed_count') or 0) + (full.get('skipped_count') or 0),
    'passed': full.get('passed_count'),
    'failed': full.get('failed_count'),
    'skipped': full.get('skipped_count'),
    'flaky': full.get('flaky'),
    'exit_code': full.get('exit_code'),
    'duration_sec': full.get('duration_sec'),
    'stats': full.get('stats'),
    'per_file_duration_ms': full.get('per_file_duration_ms'),
    'failures': failed,
    'skips': skipped,
    'rerun_runs': [{'label': r.get('label'), 'exit_code': r.get('exit_code'),
                    'passed': r.get('passed_count'), 'failed': r.get('failed_count'),
                    'skipped': r.get('skipped_count')} for r in reruns],
}
out['failed_classification'] = {f['title']: f['classification'] for f in failed}
out['status'] = ('PASS' if not failed else
                 'PASS_WITH_CLASSIFIED_NON_PRODUCT' if failed and all(
                     f['classification'] in ('HARNESS_ENV', 'REDLINE_LIMITED', 'SUITE_ASSET') for f in failed)
                 else 'FAIL')
(HERE / 'e2e-suite.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
print(json.dumps({'total': out['total'], 'passed': out['passed'], 'failed': out['failed'],
                  'skipped': out['skipped'], 'status': out['status'],
                  'failed_titles': [f['title'] for f in failed]}, ensure_ascii=False, indent=1))
