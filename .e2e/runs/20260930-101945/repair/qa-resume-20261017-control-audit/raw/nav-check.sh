#!/usr/bin/env bash
# Usage: nav-check.sh <name> <start-url> <find-spec...>
# Clicks a link (navigation only, no writes), records resulting URL + page heading.
set -uo pipefail
AUDIT_DIR="/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261017-control-audit"
SESSION="ctrlaudit-20261017"
NAME="$1"; URL="$2"; shift 2
FIND_SPEC="$*"
agent-browser --session "$SESSION" open "$URL" >/dev/null 2>&1
agent-browser --session "$SESSION" wait --load networkidle >/dev/null 2>&1 || agent-browser --session "$SESSION" wait 2000 >/dev/null 2>&1
sleep 0.8
eval "agent-browser --session \"$SESSION\" find $FIND_SPEC" >/dev/null 2>&1 || { echo "FIND-FAIL $NAME"; exit 1; }
sleep 1.2
agent-browser --session "$SESSION" eval "JSON.stringify({url: location.href, h1: document.querySelector('h1')?.innerText || '', h2: [...document.querySelectorAll('h2')].slice(0,3).map(h=>h.innerText)})" > "$AUDIT_DIR/raw/nav-$NAME.json" 2>&1
python3 - "$AUDIT_DIR/raw/nav-$NAME.json" <<'PY'
import json, sys, re
p = sys.argv[1]
raw = open(p, encoding='utf-8', errors='replace').read().strip()
try: obj = json.loads(raw)
except Exception:
    m = re.search(r'\{.*\}', raw, re.S); obj = json.loads(m.group(0)) if m else {"raw": raw}
if isinstance(obj, str):
    try: obj = json.loads(obj)
    except Exception: obj = {"raw": obj}
json.dump(obj, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(f"NAV {sys.argv[1].split('/')[-1]} -> {obj.get('url')} | {obj.get('h1')}")
PY
