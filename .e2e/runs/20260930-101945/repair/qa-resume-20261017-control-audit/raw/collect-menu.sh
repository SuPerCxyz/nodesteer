#!/usr/bin/env bash
# Usage: collect-menu.sh <name> <url> [wait-ms] -- <find args...>
# Example: collect-menu.sh menu-view http://.../agents 900 -- role button --name 视图
# Opens page, real-clicks a trigger via `find`, extracts controls, then Escape to close.
set -uo pipefail
AUDIT_DIR="/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261017-control-audit"
SESSION="ctrlaudit-20261017"
NAME="$1"; URL="$2"; WAIT_MS="${3:-900}"; shift 3
[ "${1:-}" = "--" ] && shift
FIND_SPEC="$*"
JS="$(cat "$AUDIT_DIR/raw/extract.js")"
ab() { agent-browser --session "$SESSION" "$@"; }
ab open "$URL" >/dev/null 2>&1
ab wait --load networkidle >/dev/null 2>&1 || ab wait 2000 >/dev/null 2>&1
eval "agent-browser --session \"$SESSION\" find $FIND_SPEC" >/dev/null 2>&1 || { echo "FIND-FAIL $NAME"; exit 1; }
sleep "$(echo "scale=3; $WAIT_MS/1000" | bc)"
ab eval "$JS" > "$AUDIT_DIR/raw/$NAME.json" 2>&1
ab eval "JSON.stringify({dialogs:[...document.querySelectorAll('[role=dialog], dialog[open]')].map(d=>d.innerText.replace(/\s+/g,' ').slice(0,300)), menus:[...document.querySelectorAll('[role=menu]')].map(m=>m.innerText.replace(/\s+/g,' ').slice(0,300))})" > "$AUDIT_DIR/raw/$NAME.meta.json" 2>&1
ab press Escape >/dev/null 2>&1
sleep 0.4
ab eval "JSON.stringify({remaining: document.querySelectorAll('[role=dialog], dialog[open], [role=menu]').length})" > "$AUDIT_DIR/raw/$NAME.after-esc.json" 2>&1
python3 - "$AUDIT_DIR/raw/$NAME.json" "$AUDIT_DIR/raw/$NAME.meta.json" "$AUDIT_DIR/raw/$NAME.after-esc.json" <<'PY'
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
obj = load(sys.argv[1]); meta = load(sys.argv[2]); after = load(sys.argv[3])
print(f"OK {sys.argv[1].split('/')[-1]} controls={len(obj.get('controls',[]))} menus={meta.get('menus',[])[:1]} esc_closed={after.get('remaining')}")
PY
