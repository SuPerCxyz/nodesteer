#!/usr/bin/env bash
# Usage: collect2.sh <page-list-file>
# Each line: <name> <url>
set -uo pipefail
AUDIT_DIR="/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261017-control-audit"
SESSION="ctrlaudit-20261017"
JS="$(cat "$AUDIT_DIR/raw/extract.js")"
while read -r NAME URL; do
  [ -z "$NAME" ] && continue
  agent-browser --session "$SESSION" open "$URL" >/dev/null 2>&1
  agent-browser --session "$SESSION" wait --load networkidle >/dev/null 2>&1 || agent-browser --session "$SESSION" wait 2000 >/dev/null 2>&1
  sleep 1
  agent-browser --session "$SESSION" eval "$JS" > "$AUDIT_DIR/raw/$NAME.json" 2>&1
  agent-browser --session "$SESSION" snapshot -i --json > "$AUDIT_DIR/raw/$NAME.a11y.json" 2>&1
  agent-browser --session "$SESSION" screenshot "$AUDIT_DIR/evidence/page-$NAME.png" >/dev/null 2>&1
  python3 - "$AUDIT_DIR/raw/$NAME.json" "$AUDIT_DIR/raw/$NAME.a11y.json" <<'PY'
import json, sys, re
def load(p):
    raw = open(p, encoding='utf-8', errors='replace').read().strip()
    try: obj = json.loads(raw)
    except Exception:
        m = re.search(r'\{.*\}', raw, re.S)
        obj = json.loads(m.group(0)) if m else {"raw": raw}
    if isinstance(obj, str):
        try: obj = json.loads(obj)
        except Exception: obj = {"raw": obj}
    json.dump(obj, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return obj
a = load(sys.argv[1]); b = load(sys.argv[2])
refs = (b.get('data') or {}).get('refs') or {}
print(f"OK {sys.argv[1].split('/')[-1]} js={len(a.get('controls',[]))} a11y={len(refs)}")
PY
done < "$1"
