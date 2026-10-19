#!/usr/bin/env python3
"""Attach trace paths to failures-classified.json and refresh counts."""
import json, pathlib, time

OUT = pathlib.Path('/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261018-playwright')
EV = OUT / 'evidence'

# label -> test title fragment
TRACE_MAP = {
    'trace-safe-05': 'SAFE-05', 'trace-safe-21': 'SAFE-21', 'trace-safe-23': 'SAFE-23',
    'trace-safe-24': 'SAFE-24', 'trace-gap-14': 'GAP-14', 'trace-gap-15': 'GAP-15',
    'trace-gap-17': 'GAP-17', 'trace-gap-20': 'GAP-20', 'trace-full-06': 'FULL-06',
    'trace-full-08': 'FULL-08', 'trace-full-09': 'FULL-09', 'trace-role-01': 'ROLE-01',
    'trace-role-02': 'ROLE-02',
}
REGENERABLE = {'SAFE-01': 'bash evidence/trace.sh（trace-safe-01，47MB，未入库）',
               'GAP-16': 'bash evidence/trace.sh（trace-gap-16，44MB，未入库）',
               'GAP-18': 'bash evidence/trace.sh（trace-gap-18，18MB，未入库；该用例复跑已通过）'}

d = json.loads((OUT / 'failures-classified.json').read_text())
for f in d['failures']:
    frag = f['title'].split()[0]
    hit = None
    for label, marker in TRACE_MAP.items():
        if frag == marker and (EV / 'traces' / label).exists():
            hit = str(EV.relative_to(pathlib.Path('/home/superc/code/nodesteer')) / 'traces' / label / 'trace.zip')
    f['trace_path'] = hit
    f['trace_note'] = REGENERABLE.get(frag)
    f['attempts_summary'] = [{'run': a['run'], 'status': a['status'], 'duration_ms': a['duration_ms']} for a in f['attempts']]
    f['reproduction_count'] = len(f['attempts'])

d['trace_evidence'] = {
    'stored': sorted(TRACE_MAP.keys()),
    'not_stored_large': REGENERABLE,
    'regenerate_command': 'bash evidence/trace.sh（run-pw.py --trace=on，每 label 独立输出目录）',
}
d['finalized_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
(OUT / 'failures-classified.json').write_text(json.dumps(d, ensure_ascii=False, indent=2))
print(json.dumps({'failures': len(d['failures']), 'with_trace': sum(1 for f in d['failures'] if f['trace_path']),
                  'still_failing': d['still_failing_count']}, ensure_ascii=False))
