#!/usr/bin/env python3
"""Read-only legacy deployment-journal scan for the repair fleet.

Purpose (CR-1 residual risk): enumerate DONE rows written by the mid-version
agent build (binding columns present, completed_kind missing) and evaluate
whether a harmful stale-backup replay is reachable once the CR-1 predicate is
deployed.

Read-only: opens each agent state DB with mode=ro and never writes.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools'))
import cr1lib as L  # noqa: E402

APPS_SQL = "SELECT id, revision, definition_json FROM applications"


def rows_sql(columns):
    """Adapt the projection to the schema actually deployed (completed_kind is new in CR-1)."""
    kind = 'completed_kind' if 'completed_kind' in columns else "'' AS completed_kind"
    return ("SELECT id, application_id, phase, %s, from_version, to_version, app_revision, "
            "binary_path, config_path, unit_name, backup_path, backup_config_path, backup_unit_path, "
            "started_at, updated_at FROM deployments ORDER BY application_id, started_at" % kind)


def scan_node(node, db=None):
    db = db or L.agent_db_path(node)
    cols = L.agent_db_rows(node, "SELECT name FROM pragma_table_info('deployments')", db=db)
    columns = [c.get('name') for c in cols] if isinstance(cols, list) else []
    return {
        'node': node,
        'ip': L.NODES[node],
        'unit': L.AGENT_UNIT_FMT % node,
        'db': db,
        'deployments': L.agent_db_rows(node, rows_sql(columns), db=db),
        'applications': L.agent_db_rows(node, APPS_SQL, db=db),
        'columns': cols,
    }


def binding_complete(r):
    return (int(r.get('app_revision') or 0) > 0 and (r.get('binary_path') or '')
            and (r.get('config_path') or '') and (r.get('unit_name') or ''))


def analyze(scan):
    out = {'at_utc': L.now_utc(), 'nodes': {}}
    for node, data in scan.items():
        rows = data['deployments'] if isinstance(data['deployments'], list) else []
        apps = {a['id']: a for a in (data['applications'] if isinstance(data['applications'], list) else [])}
        local_def = {}
        for aid, a in apps.items():
            try:
                d = json.loads(a['definition_json'])
            except Exception:
                d = {}
            local_def[aid] = {'revision': a.get('revision'),
                              'binary_path': d.get('binary_path'), 'config_path': d.get('config_path'),
                              'unit_name': d.get('unit_name'), 'name': d.get('name')}
        # mid-version DONE rows: binding present, cause unknown
        legacy_done = [r for r in rows
                       if r['phase'] == 'DONE' and (r.get('completed_kind') or '') == ''
                       and (int(r.get('app_revision') or 0) > 0 or binding_complete(r))]
        active = [r for r in rows if r['phase'] != 'DONE']
        by_app = {}
        for r in rows:
            by_app.setdefault(r['application_id'], []).append(r)
        risky = []
        for r in active:
            if not binding_complete(r):
                continue
            aid = r['application_id']
            later = [o for o in by_app.get(aid, [])
                     if o['id'] != r['id'] and o['phase'] == 'DONE' and o['started_at'] > r['started_at']]
            later_unknown = [o for o in later if (o.get('completed_kind') or '') == '']
            later_deployed = [o for o in later if (o.get('completed_kind') or '') == 'deployed']
            if not later_unknown:
                continue
            loc = local_def.get(aid)
            binding_match = bool(loc and loc.get('revision') == int(r.get('app_revision') or 0)
                                 and loc.get('binary_path') == r.get('binary_path')
                                 and loc.get('config_path') == r.get('config_path')
                                 and loc.get('unit_name') == r.get('unit_name'))
            risky.append({
                'node': node, 'application_id': aid, 'application_name': (loc or {}).get('name'),
                'active_row': {k: r.get(k) for k in ('id', 'phase', 'app_revision', 'binary_path',
                                                     'config_path', 'unit_name', 'backup_path', 'started_at')},
                'later_unknown_done_rows': [{'id': o['id'], 'started_at': o['started_at'],
                                             'backup_path': o['backup_path'],
                                             'phase': o['phase']} for o in later_unknown],
                'later_deployed_done_rows': len(later_deployed),
                'local_definition_present': bool(loc),
                'binding_matches_current_local_definition': binding_match,
                'replay_reachable': binding_match,
            })
        out['nodes'][node] = {
            'db': data['db'],
            'row_count': len(rows),
            'completed_kind_column_present': any(
                (c.get('name') == 'completed_kind') for c in (data['columns'] if isinstance(data['columns'], list) else [])),
            'legacy_done_unknown_cause': [
                {k: r.get(k) for k in ('id', 'application_id', 'phase', 'completed_kind', 'app_revision',
                                       'binary_path', 'config_path', 'unit_name', 'backup_path', 'started_at')}
                for r in legacy_done],
            'active_rows': [
                {k: r.get(k) for k in ('id', 'application_id', 'phase', 'completed_kind', 'app_revision',
                                       'binary_path', 'config_path', 'unit_name', 'backup_path', 'started_at')}
                for r in active],
            'local_application_definitions': len(local_def),
            'risky_pairs': risky,
        }
    return out


def snapshot(tag):
    scan = {n: scan_node(n) for n in L.NODES}
    (L.ev_dir() / ('legacy-scan-%s.json' % tag)).write_text(
        json.dumps(scan, ensure_ascii=False, indent=1) + '\n')
    return scan


if __name__ == '__main__':
    tag = sys.argv[1] if len(sys.argv) > 1 else 'adhoc'
    scan = snapshot(tag)
    summary = analyze(scan)
    L.ev_write('legacy-audit-%s.json' % tag, summary)
    for node, d in summary['nodes'].items():
        print('%s rows=%s kind_col=%s legacy_done=%s active=%s risky=%s local_defs=%s' % (
            node, d['row_count'], d['completed_kind_column_present'],
            len(d['legacy_done_unknown_cause']), len(d['active_rows']),
            len(d['risky_pairs']), d['local_application_definitions']))
