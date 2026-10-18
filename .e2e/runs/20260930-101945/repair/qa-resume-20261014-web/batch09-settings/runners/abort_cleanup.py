#!/usr/bin/env python3
"""Emergency cleanup for an interrupted batch09-settings run.

Restores the four managed runtime settings to their known originals, removes the
owned child Agent process/root on the child host and deletes the owned child node
from the Hub. Idempotent: safe to run when everything is already clean.

Usage: abort_cleanup.py <nonce> [child_node_id]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import childlib  # noqa: E402
import hubapi  # noqa: E402

ORIGINAL = {'heartbeat_interval_sec': '30', 'revision_check_interval_sec': '45',
            'changelog_window': '5000', 'max_log_bytes': '1048576'}


def main():
    nonce = sys.argv[1] if len(sys.argv) > 1 else None
    node_id = sys.argv[2] if len(sys.argv) > 2 else None
    hub = hubapi.Hub()
    hub.login()
    out = {'at_utc': __import__('time').strftime('%Y-%m-%dT%H:%M:%SZ', __import__('time').gmtime())}
    out['api_settings_before'] = hub.json('/api/settings')
    code, body = hub.put('/api/settings', ORIGINAL)
    out['settings_put_http'] = code
    out['api_settings_after'] = hub.json('/api/settings')
    out['settings_ok'] = out['api_settings_after'] == ORIGINAL
    if nonce:
        child = childlib.OwnedChild(nonce, 1, '', '', '', '', '', tag='qa-b09-settings')
        out['child_removal'] = child.remove()
    if node_id:
        dcode, dbody = hub.delete('/api/nodes/' + node_id)
        gcode, _ = hub.get('/api/nodes/' + node_id)
        out['child_node'] = {'id': node_id, 'delete_http': dcode, 'get_http': gcode}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out['settings_ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
