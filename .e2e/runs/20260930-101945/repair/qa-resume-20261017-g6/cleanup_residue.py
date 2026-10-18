#!/usr/bin/env python3
"""Manual residue cleanup for the qa-resume-20261017-g6 batch (used when a
phase aborts before phase8_cleanup).  Owned objects only: hub objects whose name
starts with the batch prefix, and owned child roots/processes on native-1 whose
path matches /var/lib/nodesteer-qa-g6-<nonce>-*.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import child_agent  # noqa: E402
import hubapi  # noqa: E402

HERE = Path(__file__).resolve().parent
NONCE = (HERE / '.nonce').read_text().strip()
PREFIX = 'qa-g6-%s-' % NONCE


def main():
    hub = hubapi.Hub()
    hub.login()
    out = {'prefix': PREFIX, 'children': {}, 'deleted': {}}
    for label in ('a', 'b', 'c'):
        child = child_agent.OwnedChild(NONCE, 0, '', '', PREFIX + label, child_agent.HOST, '', label=label)
        try:
            out['children'][label] = child.remove()
        except Exception as exc:
            out['children'][label] = {'_error': '%s: %s' % (type(exc).__name__, exc)}
    bykind = {}
    for kind, listing in (('task', hub.json('/api/tasks')), ('script', hub.json('/api/scripts')),
                          ('schedule', hub.json('/api/schedules')), ('group', hub.json('/api/groups')),
                          ('application', hub.json('/api/applications'))):
        bykind[kind] = [x for x in listing if (x.get('name') or '').startswith(PREFIX)]
    for kind in ('task', 'script', 'schedule', 'group', 'application'):
        for x in bykind[kind]:
            code, body = hub.delete('/api/%ss/%s' % (kind, hub.quote(x['id'])))
            out['deleted'].setdefault(kind, []).append({'id': x['id'], 'name': x['name'], 'http': code,
                                                        'body': body if code not in (200, 201) else None})
    for n in hub.json('/api/nodes'):
        if (n.get('hostname') or '').startswith(PREFIX):
            code, body = hub.delete('/api/nodes/' + hub.quote(n['id']))
            out['deleted'].setdefault('node', []).append({'id': n['id'], 'name': n['hostname'], 'http': code})
    import json
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
