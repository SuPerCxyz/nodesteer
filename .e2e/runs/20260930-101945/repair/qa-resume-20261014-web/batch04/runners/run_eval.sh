#!/usr/bin/env bash
# Usage: run_eval.sh <session> <js-file> <out-json-file>
set -euo pipefail
S="$1"; JS="$2"; OUT="$3"
cat "$JS" | agent-browser --session "$S" eval --stdin > "$OUT.raw" 2>"$OUT.err" || true
python3 - "$OUT" <<'PY'
import json,sys
out=sys.argv[1]
raw=open(out+'.raw').read().strip()
try: v=json.loads(raw)
except Exception: v={"__eval_raw": raw}
if isinstance(v,str):
    try: v=json.loads(v)
    except Exception: v={"__eval_str": v}
json.dump(v,open(out,'w'),ensure_ascii=False,indent=1)
print(json.dumps(v,ensure_ascii=False,indent=1)[:6000])
PY
