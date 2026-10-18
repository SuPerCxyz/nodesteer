#!/usr/bin/env python3
"""Correction pass for two harness mistakes in the first api phase run.

1. content boundary string was 262146 B instead of exactly 262144 B.
2. /metrics is registered without the /api prefix.

Re-runs only the affected checks, updates the stored case rows in place, writes the
missing evidence JSONs, and regenerates regression-results.json.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import regression_run as rr  # noqa: E402  (module load only; __main__ guard)

hub = rr.hub
hub.login()
P = rr.P
out = {'at_utc': rr.now_utc(), 'note': 'correction pass for boundary/metrics harness bugs'}

# --- P1-4 boundary recheck (exact 262144 -> 200, 262145 -> 400, UTF-8 multibyte) ---
content_at = '#!/bin/sh\n# ' + ('a' * (262144 - 13)) + '\n'
code, body = hub.post('/api/scripts', rr._script_body(P + 'script-at2', content=content_at))
out['content_at'] = {'bytes': len(content_at), 'http': code,
                     'id': (body or {}).get('id') if isinstance(body, dict) else None}
at_id = out['content_at']['id']
if at_id:
    rr.ST['fixtures'].append(('script', at_id, P + 'script-at2'))
content_over = '#!/bin/sh\n# ' + ('a' * (262145 - 13)) + '\n'
code, body = hub.post('/api/scripts', rr._script_body(P + 'script-over2', content=content_over))
out['content_over'] = {'bytes': len(content_over), 'http': code, 'body': body}
content_mb = '#!/bin/sh\n# ' + ('\u6c49' * ((262145 // 3) + 1))
code, body = hub.post('/api/scripts', rr._script_body(P + 'script-mb', content=content_mb))
out['content_multibyte'] = {'bytes': len(content_mb.encode('utf-8')), 'http': code, 'body': body}
out['at_ok'] = out['content_at']['http'] in (200, 201) and len(content_at) == 262144
out['over_ok'] = out['content_over']['http'] == 400
out['mb_ok'] = out['content_multibyte']['http'] == 400

# --- P3-7 metrics recheck on the real path ---
code, hdrs, _ = hub.raw_text('GET', '/metrics')
metrics_get = {'http': code, 'path': '/metrics'}
code, hdrs, _ = hub.raw_text('POST', '/metrics')
metrics_post = {'http': code, 'allow': hdrs.get('Allow'), 'path': '/metrics'}
out['metrics'] = {'GET': metrics_get, 'POST': metrics_post}
out['metrics_ok'] = metrics_get['http'] == 200 and metrics_post['http'] == 405 and 'GET' in (metrics_post['allow'] or '')

# --- update stored case rows ---
for case in rr.ST['cases']:
    if case['case_id'] == 'P07_P08-11/API-CONTRACT':
        ex = case['extra']
        ex['content_at'] = out['content_at']
        ex['content_at_bytes'] = len(content_at)
        ex['content_at_ok'] = out['at_ok']
        ex['content_over'] = out['content_over']
        ex['content_over_ok'] = out['over_ok']
        ex['content_multibyte'] = out['content_multibyte']
        ex['content_multibyte_ok'] = out['mb_ok']
        ex['correction_pass'] = 'first run used 262146/262147 B and /api/metrics; corrected here'
        ok = all(ex[k] for k in ('neg_timeout_ok', 'zero_timeout_ok', 'content_at_ok',
                                 'content_over_ok', 'content_multibyte_ok', 'neg_timeout_update_ok'))
        case['new_status'] = 'PASS' if ok else 'FAIL'
        case['new_conclusion'] = ('timeout=-5→400 且不落库；timeout=0→200 且回读 300；content 262144B→200、'
                                  '262145B→400、多字节超限→400；PUT 负数→400 且不改变已存值')
        case['extra'] = ex
    if case['case_id'] == 'API-CONTRACT/P19-05/FL-07':
        ex = case['extra']
        ex['metrics_POST'] = metrics_post
        ex['metrics_GET'] = metrics_get
        ok = (all(ex['audit_' + x]['http'] == 405 and 'GET' in (ex['audit_' + x]['allow'] or '')
                  for x in ('POST', 'PUT', 'DELETE', 'PATCH'))
              and ex['audit_GET']['http'] == 200 and bool(ex['audit_GET']['x_total_count'])
              and ex['audit_OPTIONS']['http'] == 204
              and ex['me_POST']['http'] == 405 and 'GET' in (ex['me_POST']['allow'] or '')
              and out['metrics_ok']
              and ex['logout_GET']['http'] == 405 and ex['me_still_valid_after_logout_GET']
              and ex['logout_POST']['http'] == 200)
        case['new_status'] = 'PASS' if ok else 'FAIL'
        case['extra'] = ex
        case['note'] = 'metrics 端点实为 /metrics（无 /api 前缀）'

rr.save_state(rr.ST)
rr.dump_results()
# evidence files for the api cases
for case in rr.ST['cases']:
    if case['case_id'] == 'P07_P08-11/API-CONTRACT':
        rr.ev_write('api-p1-4.json', case['extra'])
    if case['case_id'] == 'P06-02/P09P11-10/API-CONTRACT':
        rr.ev_write('api-p2-5.json', case['extra'])
    if case['case_id'] == 'API-CONTRACT/P12P13-02/P14P15-01':
        rr.ev_write('api-p2-6.json', case['extra'])
    if case['case_id'] == 'API-CONTRACT/P19-05/FL-07':
        rr.ev_write('api-p3-7.json', case['extra'])
print(json.dumps(out, ensure_ascii=False, indent=1))
print('cases:', [(c['case_id'], c['new_status']) for c in rr.ST['cases']])
