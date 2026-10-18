#!/usr/bin/env bash
# Drive one transfer: click 开始传输 on the prepared form, then cancel the owned row
# from its 操作 menu while polling the server status timeline.
# Usage: run_cancel_flow.sh <tag> <marker> [poll_seconds] [delay_before_cancel_ms]
set -u
B=/home/superc/code/nodesteer/.e2e/runs/20260930-101945/repair/qa-resume-20261014-web/batch04
S=qaweb1410b04
TAG="$1"; MARKER="$2"; POLL="${3:-45}"; DELAY_MS="${4:-0}"
cd "$B" || exit 1
TMP=/tmp/opencode
python3 runners/poll_transfers.py "evidence/${TAG}-timeline.json" "$POLL" > "$TMP/${TAG}-poll.log" 2>&1 &
POLL_PID=$!
sleep 0.4
agent-browser --session "$S" network requests --clear >/dev/null 2>&1
agent-browser --session "$S" find role button click --name "开始传输" >"$TMP/${TAG}-startclick.txt" 2>&1
if [ "$DELAY_MS" != "0" ]; then sleep "$(python3 -c "print($DELAY_MS/1000.0)")"; fi
python3 - "$MARKER" > "$TMP/${TAG}-marker.js" <<'PY'
import json,sys
print("window.__b04Marker=%s; 'marker-set'" % json.dumps(sys.argv[1]))
PY
agent-browser --session "$S" eval --stdin < "$TMP/${TAG}-marker.js" >"$TMP/${TAG}-marker.json" 2>&1
agent-browser --session "$S" eval --stdin < runners/cancel_row_transfer.js >"$TMP/${TAG}-cancel.raw" 2>&1
agent-browser --session "$S" network requests --json >"$TMP/${TAG}-net.json" 2>/dev/null
wait "$POLL_PID"
python3 - "$TMP/${TAG}-cancel.raw" "$TAG" <<'PY'
import json,sys
raw=open(sys.argv[1]).read().strip()
v=None
try: v=json.loads(raw)
except Exception:
    # agent-browser may prefix a checkmark line; retry on the JSON object only
    i=raw.find('{')
    if i>=0:
        try: v=json.loads(raw[i:])
        except Exception: v=None
if isinstance(v,str):
    try: v=json.loads(v)
    except Exception: pass
print(sys.argv[2],"cancel-eval:",json.dumps(v,ensure_ascii=False))
PY
