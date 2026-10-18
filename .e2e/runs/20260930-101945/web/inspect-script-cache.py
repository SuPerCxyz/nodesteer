import json, re, subprocess, sys
from pathlib import Path
script_id=sys.argv[1]
assert re.fullmatch(r"[a-f0-9-]{36}",script_id)
private=Path("/tmp/nodesteer-e2e-20260930-101945-7wksl0_x")
c=json.loads((private/"connection.json").read_text())
a=next(v for v in c["agents"] if v["node_id"]=="56765828-4896-492b-b562-64afdc0fb8d6")
data=json.dumps({"directory":a["data_dir"],"script_id":script_id})
payload="python3 - <<'PYCACHE'\nimport json,pathlib,sqlite3\nargs=json.loads("+repr(data)+")\nd=pathlib.Path(args['directory']);db=d/'state.db'\ns=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)\ncount=s.execute('SELECT count(*) FROM scripts WHERE id=?',(args['script_id'],)).fetchone()[0]\nfiles=[str(p.relative_to(d)) for p in (d/'scripts').glob('*'+args['script_id']+'*')]\nprint(json.dumps({'script_id':args['script_id'],'rows':count,'files':files}))\nPYCACHE\n"
helper=Path(__file__).parent.parent/"environment/guest-exec.py"
r=subprocess.run([sys.executable,str(helper),"--node",a["node_id"],"--timeout","30"],input=payload,capture_output=True,text=True,timeout=35)
if r.returncode:raise RuntimeError(r.stderr[:300])
print(r.stdout)
