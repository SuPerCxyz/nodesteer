#!/usr/bin/env bash
# Usage: collect.sh <page-name> <url> [wait-ms]
# Read-only page control collection. Never clicks write actions.
set -uo pipefail
AUDIT_DIR="/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261017-control-audit"
SESSION="ctrlaudit-20261017"
NAME="$1"
URL="$2"
WAIT_MS="${3:-1200}"
JS="$(cat "$AUDIT_DIR/raw/extract.js")"
ab() { agent-browser --session "$SESSION" "$@"; }
ab open "$URL" >/dev/null 2>&1
ab wait --load networkidle >/dev/null 2>&1 || ab wait 2000 >/dev/null 2>&1
sleep "$(echo "scale=3; $WAIT_MS/1000" | bc)"
ab eval "$JS" > "$AUDIT_DIR/raw/$NAME.json" 2>&1
# Normalize: strip possible wrapper text, keep JSON only
python3 - "$AUDIT_DIR/raw/$NAME.json" <<'PY'
import json, sys, re
p = sys.argv[1]
raw = open(p, encoding='utf-8', errors='replace').read()
raw = raw.strip()
# agent-browser eval may print the JSON string as a quoted JSON string
try:
    obj = json.loads(raw)
except Exception:
    m = re.search(r'\{.*\}', raw, re.S)
    if not m:
        print(f"PARSE-FAIL {p}: {raw[:200]}")
        sys.exit(1)
    obj = json.loads(m.group(0))
if isinstance(obj, str):
    try:
        obj = json.loads(obj)
    except Exception:
        obj = {"raw": obj}
json.dump(obj, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
n = len(obj.get('controls', [])) if isinstance(obj, dict) else 0
print(f"OK {p.split('/')[-1]} controls={n}")
PY
