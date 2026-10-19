#!/usr/bin/env python3
"""Parse Playwright raw JSON per-file results into per-test-results.json."""
import json, pathlib, re, sys

WORK = pathlib.Path('/tmp/opencode/nodesteer-pw-20261018')
RAW = WORK / 'raw'
OUT = pathlib.Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-playwright')

rows = []
run_meta = []
for runfile in sorted(RAW.glob('*-run.json')):
    label = runfile.name[:-len('-run.json')]
    meta = json.loads(runfile.read_text())
    run_meta.append(meta)
    raw = RAW / f'{label}.json'
    if not raw.exists():
        continue
    try:
        data = json.loads(raw.read_text())
    except ValueError:
        continue
    for suite in data.get('suites', []):
        def walk(node, filetitle):
            title = node.get('title')
            if 'specs' in node:
                for spec in node['specs']:
                    for t in spec.get('tests', []):
                        for res in t.get('results', []):
                            errs = res.get('errors') or ([res['error']] if res.get('error') else [])
                            msg = ' | '.join(
                                (e.get('message') or '').strip().replace('\n', ' ')[:1500]
                                for e in errs
                            )
                            an = (res.get('attachments') or [])
                            rows.append({
                                'file': label,
                                'suite': filetitle,
                                'title': spec.get('title'),
                                'spec_file': spec.get('file'),
                                'line': spec.get('line'),
                                'project': t.get('projectName'),
                                'status': res.get('status'),
                                'expected_status': t.get('expectedStatus'),
                                'outcome': t.get('status'),
                                'duration_ms': res.get('duration'),
                                'start_time': res.get('startTime'),
                                'retry': res.get('retry'),
                                'error': msg,
                                'attachments': [a.get('path') for a in an if a.get('path')],
                            })
            for child in node.get('suites', []) or []:
                walk(child, node.get('title') or filetitle)
        walk(suite, suite.get('title'))

(OUT / 'per-test-results.json').write_text(json.dumps({
    'generated_at': __import__('time').strftime('%Y-%m-%dT%H:%M:%S%z'),
    'runs': run_meta,
    'tests': rows,
}, ensure_ascii=False, indent=2))

counts = {}
for r in rows:
    counts[r['status']] = counts.get(r['status'], 0) + 1
print('total test results:', len(rows))
print('by status:', json.dumps(counts, ensure_ascii=False))
for r in rows:
    if r['status'] != 'passed':
        print(f"  [{r['status']}] {r['file']} :: {r['title'][:90]}")
