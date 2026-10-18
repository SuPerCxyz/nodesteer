"""只回报匹配文件与来源数量，绝不打印凭据值。"""
import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
RUN = ROOT.parent
PROJECT = RUN.parents[2]
PRIVATE = Path('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x')
secret_key = re.compile(r'(^|_)(password|token|credential|secret)(_|$)', re.I)
values = set()
source_errors = []


def collect(data):
    if isinstance(data, dict):
        for key, value in data.items():
            if secret_key.search(key) and isinstance(value, str) and len(value) >= 8:
                values.add(value)
            collect(value)
    elif isinstance(data, list):
        for item in data:
            collect(item)


for path in list(PRIVATE.rglob('*.json')) + list(PRIVATE.rglob('*.jsonl')):
    try:
        if path.suffix == '.jsonl':
            for line in path.read_text().splitlines():
                if line:
                    collect(json.loads(line))
        else:
            collect(json.loads(path.read_text()))
    except (OSError, ValueError):
        source_errors.append({'kind': 'private_source_unreadable', 'path': str(path)})

cfg = json.loads((PRIVATE / 'connection.json').read_text())
for node in cfg['nodes']:
    if node['id'] not in {
        '56765828-4896-492b-b562-64afdc0fb8d6', 'd77e2440-919f-4431-a488-733817dcd809',
        '811d72ef-75f8-47ba-84de-8cb1e6974315', '1c59cf12-97ef-412f-9a06-aea04e6423c1',
    }:
        continue
    proc = subprocess.run([sys.executable, str(RUN / 'environment/guest-exec.py'), '--node', node['id'], '--timeout', '20'],
                          input="""python3 - <<'PY'
import json,sqlite3
conn=sqlite3.connect('file:/var/lib/nodesteer/state.db?mode=ro',uri=True)
print(json.dumps(dict(conn.execute("SELECT key,value FROM identity WHERE key='credential'"))))
PY
""", text=True, capture_output=True, timeout=25)
    try:
        assert proc.returncode == 0
        data = json.loads(proc.stdout)
        assert data.get('credential')
        collect(data)
    except (AssertionError, ValueError):
        source_errors.append({'kind': 'agent_identity_source_unreadable', 'node_id': node['id']})

variants = {form for value in values for form in [value, quote(value, safe=''), html.escape(value), json.dumps(value, ensure_ascii=False)[1:-1]]}
paths = set(RUN.rglob('*')) | set((PROJECT / 'web/e2e').rglob('*')) | set((PROJECT / '.e2e').glob('*'))
findings = []
scanned = 0
for path in sorted(paths):
    if not path.is_file() or path.suffix not in {'.json', '.jsonl', '.md', '.py', '.js', '.mjs', '.ts', '.tsx', '.txt', '.log', '.sh', '.yaml', '.yml', '.html'}:
        continue
    content = path.read_text(errors='replace')
    scanned += 1
    if any(value in content for value in variants):
        findings.append({'path': str(path.relative_to(PROJECT)), 'kind': 'known_secret_value_match'})

output = {'time': datetime.now(timezone.utc).isoformat(), 'run_id': RUN.name,
          'scope': '当前新增run/全局E2E记录/web测试文字与当前私密JSON、四Native真实身份值及转义变体',
          'files_scanned': scanned, 'sensitive_source_values': len(values),
          'findings': findings, 'source_errors': source_errors,
          'status': 'PASS' if not findings and not source_errors else 'NEEDS_REVIEW',
          'limitation': '文字已知值匹配，不包含图片；旧/tmp消失的历史随机值无法重建，其恢复前扫描证据另保留，不声称所有可能秘密均已穷举。'}
(ROOT / 'resumed-secret-scan.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(output, ensure_ascii=False))
if findings or source_errors:
    sys.exit(1)
