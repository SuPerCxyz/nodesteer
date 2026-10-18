#!/usr/bin/env python3
"""qa-resume-20261017-fixall-verify: CR-3 runtime verification on the owned child agent.

Injects the four unreplayable deployment-journal row types into the owned child's
local store (owned data dir; child stopped), then restarts the deployed agent
binary twice and checks:
 1. first restart terminalizes all four rows (DONE + completed_kind) and logs the
    per-kind close + summary lines, no "still active" line;
 2. second restart adds zero new recovery lines -> no repeated alerts.

The four rows cannot be produced through the product path on a live environment
(they are legacy/drift states), so the journal state is constructed in the owned
store while the real agent binary performs the real recovery code path.
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'evidence' / 'tools'))
sys.path.insert(0, str(HERE))
import hostlib  # noqa: E402
import regression_api as A  # noqa: E402

EV = HERE / 'evidence'
os.umask(0o077)

APP_SUP = 'fixall-cr3-superseded'
APP_STALE = 'fixall-cr3-stale'
APP_LEGACY = 'fixall-cr3-legacy'
APP_PREP = 'fixall-cr3-preparing'

INJECT = r'''
import json, os, sqlite3, time
data = %(data)r
db = os.path.join(data, 'state.db')
def defn(app_id, binary_path, unit_name):
    return json.dumps({'id': app_id, 'name': app_id, 'version': '1.0',
                       'binary_path': binary_path, 'config_path': '',
                       'unit_name': unit_name, 'artifact_id': 'a1'})
now = time.strftime('%%Y-%%m-%%dT%%H:%%M:%%SZ', time.gmtime())
conn = sqlite3.connect(db)
conn.execute('PRAGMA foreign_keys=ON')
apps = [
    (%(sup)r, defn(%(sup)r, '/usr/local/bin/fixall-sup', 'nodesteer-fixall-sup.service'), 1),
    (%(stale)r, defn(%(stale)r, '/usr/local/bin/fixall-stale', 'nodesteer-fixall-stale.service'), 2),
    (%(legacy)r, defn(%(legacy)r, '/usr/local/bin/fixall-legacy', 'nodesteer-fixall-legacy.service'), 1),
    (%(prep)r, defn(%(prep)r, '/usr/local/bin/fixall-prep', 'nodesteer-fixall-prep.service'), 1),
]
for app_id, d, rev in apps:
    conn.execute('INSERT OR REPLACE INTO applications (id, definition_json, revision) VALUES (?,?,?)',
                 (app_id, d, rev))
rows = [
    # superseded: earlier incomplete row + later DONE/deployed row (same app)
    ('cr3-sup-row1', %(sup)r, '0.9', '1.0', 'STOPPED', '', '', '', '', 1,
     '/usr/local/bin/fixall-sup', '/etc/nodesteer-fixall-sup.conf', 'nodesteer-fixall-sup.service',
     '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'),
    ('cr3-sup-row2', %(sup)r, '1.0', '1.1', 'DONE', 'deployed', '', '', '', 1,
     '/usr/local/bin/fixall-sup', '/etc/nodesteer-fixall-sup.conf', 'nodesteer-fixall-sup.service',
     '2026-10-02T00:00:00Z', '2026-10-02T00:00:00Z'),
    # stale binding: binding present but does not match the definition
    ('cr3-stale-row1', %(stale)r, '1.0', '1.1', 'STOPPED', '', '', '', '', 2,
     '/usr/local/bin/fixall-OTHER', '/etc/nodesteer-fixall-stale.conf', 'nodesteer-fixall-stale.service',
     '2026-10-01T01:00:00Z', '2026-10-01T01:00:00Z'),
    # legacy unbound: no binding fields
    ('cr3-legacy-row1', %(legacy)r, '0.9', '1.0', 'STOPPED', '', '', '', '', 0,
     '', '', '', '2026-10-01T02:00:00Z', '2026-10-01T02:00:00Z'),
    # preparing interrupted
    ('cr3-prep-row1', %(prep)r, '1.0', '1.1', 'PREPARING', '', '', '', '', 1,
     '/usr/local/bin/fixall-prep', '/etc/nodesteer-fixall-prep.conf', 'nodesteer-fixall-prep.service',
     '2026-10-01T03:00:00Z', '2026-10-01T03:00:00Z'),
]
for r in rows:
    conn.execute('INSERT OR REPLACE INTO deployments (id, application_id, from_version, to_version, phase, '
                 'completed_kind, backup_path, backup_config_path, backup_unit_path, app_revision, binary_path, '
                 'config_path, unit_name, started_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', r)
conn.commit()
count = conn.execute("SELECT COUNT(*) FROM deployments WHERE phase != 'DONE'").fetchone()[0]
print(json.dumps({'injected': len(rows), 'active_rows': count}))
'''

CHECK = r'''
import json, os, sqlite3
data = %(data)r
db = os.path.join(data, 'state.db')
conn = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
conn.execute('PRAGMA query_only=ON')
rows = [{'id': r[0], 'phase': r[1], 'completed_kind': r[2]} for r in conn.execute(
    "SELECT id, phase, completed_kind FROM deployments WHERE id LIKE 'cr3-%%' ORDER BY id")]
active = [r[0] for r in conn.execute("SELECT id FROM deployments WHERE phase != 'DONE'")]
print(json.dumps({'rows': rows, 'active_rows': active}))
'''


def count_lines(text, needle):
    return sum(1 for l in text.splitlines() if needle in l)


def main():
    A.hub.login()
    meta = A.ST['children']['a']
    ch = A.child_of('a')
    out = {'at_utc': A.now_utc(), 'case': ['CR-3'], 'child': {'node_id': meta['node_id'], 'root': ch.root}}
    # stop the owned child, inject rows into its owned store, restart
    out['stop1'] = ch.stop()
    time.sleep(2)
    out['inject'] = ch.remote_py(INJECT % {'data': ch.data, 'sup': APP_SUP, 'stale': APP_STALE,
                                           'legacy': APP_LEGACY, 'prep': APP_PREP})
    log_before = (ch.log_tail(200000).get('tail') or '')
    base_closed = count_lines(log_before, 'unreplayable deployment journal closed')
    base_summary = count_lines(log_before, 'unreplayable deployment journals closed by recovery')
    out['start1'] = ch.start()
    deadline = time.time() + 60
    while time.time() < deadline:
        time.sleep(3)
        tail = ch.log_tail(200000).get('tail') or ''
        if count_lines(tail, 'unreplayable deployment journals closed by recovery') > base_summary:
            break
    time.sleep(3)
    tail1 = ch.log_tail(200000).get('tail') or ''
    new1 = tail1[len(log_before):] if tail1.startswith(log_before) else tail1
    out['restart1'] = {
        'closed_lines': count_lines(new1, 'unreplayable deployment journal closed'),
        'kinds': [l for l in new1.splitlines() if 'unreplayable deployment journal closed' in l],
        'summary_lines': [l for l in new1.splitlines() if 'unreplayable deployment journals closed by recovery' in l],
        'still_active_lines': [l for l in new1.splitlines() if 'deployment journals still active after recovery' in l],
        'lacks_definition_lines': [l for l in new1.splitlines() if 'lacks application definition' in l],
    }
    out['db_after1'] = ch.remote_py(CHECK % {'data': ch.data})
    # second restart (equivalent of a normal reboot) -> zero new recovery lines
    out['stop2'] = ch.stop()
    time.sleep(2)
    log_before2 = (ch.log_tail(200000).get('tail') or '')
    out['start2'] = ch.start()
    time.sleep(12)
    tail2 = ch.log_tail(200000).get('tail') or ''
    new2 = tail2[len(log_before2):] if tail2.startswith(log_before2) else tail2
    out['restart2'] = {
        'closed_lines': count_lines(new2, 'unreplayable deployment journal closed'),
        'summary_lines': count_lines(new2, 'unreplayable deployment journals closed by recovery'),
        'still_active_lines': count_lines(new2, 'deployment journals still active after recovery'),
        'tail': new2[-1500:],
    }
    out['db_after2'] = ch.remote_py(CHECK % {'data': ch.data})
    kinds = {r['id']: r['completed_kind'] for r in out['db_after1']['rows']}
    out['verdict'] = {
        'injected_four_active_rows': out['inject'].get('active_rows', 0) >= 4,
        'restart1_closed_four': out['restart1']['closed_lines'] == 4,
        'restart1_summary_present': len(out['restart1']['summary_lines']) == 1,
        'restart1_no_still_active': not out['restart1']['still_active_lines'],
        'terminal_kinds_correct': (kinds.get('cr3-sup-row1') == 'superseded'
                                   and kinds.get('cr3-stale-row1') == 'stale_binding'
                                   and kinds.get('cr3-legacy-row1') == 'legacy_unbound'
                                   and kinds.get('cr3-prep-row1') == 'prepare_interrupted'),
        'active_rows_empty_after1': not out['db_after1']['active_rows'],
        'restart2_no_new_alerts': out['restart2']['closed_lines'] == 0 and out['restart2']['summary_lines'] == 0,
        'restart2_no_new_still_active': out['restart2']['still_active_lines'] == 0,
    }
    ok = all(out['verdict'].values())
    A.record('CR-3-UNREPLAYABLE-ROWS-TERMINALIZED', 'cr3', 'FIXED' if ok else 'STILL_BROKEN',
             '四类不可重放行首次恢复即落终态（DONE+对应 completed_kind）、离开活动集合；'
             '第二次重启零新增告警（不重复告警）' if ok else '偏差：%s' % out['verdict'],
             ['evidence/cr3-recovery.json'], out)
    A.ev_write('cr3-recovery.json', out)
    A.dump_results()
    print(json.dumps(out['verdict'], indent=1))
    return out


if __name__ == '__main__':
    main()
