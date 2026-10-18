import hashlib
import json
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PRIVATE = Path('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json')
OUT = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend/script-revisions')
SCRIPT_IDS = [
    '154713f0-0301-4f94-a38e-87a45af5fcdd',
    'f6eb32ef-5375-49e8-a91a-1f9ea2185d29',
    '592cfc4b-3a6b-4f16-a790-74f66d14259e',
    '4779bd1a-c341-47f1-9353-3787cb05d3b0',
    'f34360e1-97d2-437a-8d47-d46bd20234fe',
]


def fetch(url, token):
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b''


def main():
    os.umask(0o077)
    if not PRIVATE.is_file() or stat.S_IMODE(PRIVATE.stat().st_mode) != 0o600:
        raise SystemExit('private_connection_file_mode_gate_failed')
    data = json.loads(PRIVATE.read_text())
    hub = data.get('hub_url')
    token = data.get('token')
    if hub != 'http://192.168.100.209:18080' or not isinstance(token, str) or not token:
        raise SystemExit('private_connection_authority_or_token_gate_failed')
    OUT.mkdir(mode=0o700, parents=True, exist_ok=True)
    results = []
    for script_id in SCRIPT_IDS:
        url = hub + '/api/scripts/' + urllib.parse.quote(script_id, safe='') + '/revisions'
        status, raw = fetch(url, token)
        row = {'script_id': script_id, 'http': status, 'revisions_recovered': 0, 'source_hash_matches': []}
        if status == 200:
            try:
                entries = json.loads(raw)
            except (TypeError, ValueError):
                entries = None
            if isinstance(entries, list):
                for entry in entries:
                    content = entry.get('content')
                    revision = entry.get('revision')
                    sha256 = entry.get('sha256')
                    if not isinstance(content, str) or not isinstance(revision, int) or not isinstance(sha256, str):
                        continue
                    if 'qaSecret-' in content or 'QA_ONLY_SECRET_MARKER_' in content:
                        row['unsafe_literal_marker_source_rejected'] = True
                        continue
                    actual_sha = hashlib.sha256(content.encode()).hexdigest()
                    artifact = {
                        'script_id': script_id,
                        'revision': revision,
                        'sha256': sha256,
                        'source_sha256_recomputed': actual_sha,
                        'content_sha_matches': actual_sha == sha256,
                        'content': content,
                    }
                    target = OUT / f'{script_id}-r{revision}.json'
                    target.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + '\n')
                    os.chmod(target, 0o600)
                    row['revisions_recovered'] += 1
                    row['source_hash_matches'].append(actual_sha == sha256)
        results.append(row)
    summary = {
        'authority': 'http://192.168.100.209:18080',
        'method': 'authenticated read-only GET /api/scripts/{id}/revisions',
        'credential_values_recorded': False,
        'task_parameter_values_read': False,
        'script_ids': results,
        'recovered_revision_count': sum(x['revisions_recovered'] for x in results),
        'all_recovered_source_hashes_match': all(v for x in results for v in x['source_hash_matches']),
    }
    path = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261008-backend/revision-recovery-summary.json')
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    os.chmod(path, 0o600)
    print(json.dumps({
        'recovered_revision_count': summary['recovered_revision_count'],
        'all_recovered_source_hashes_match': summary['all_recovered_source_hashes_match'],
        'http_statuses': [x['http'] for x in results],
        'revision_counts': [x['revisions_recovered'] for x in results],
        'credential_values_recorded': False,
    }, separators=(',', ':')))


if __name__ == '__main__':
    main()
