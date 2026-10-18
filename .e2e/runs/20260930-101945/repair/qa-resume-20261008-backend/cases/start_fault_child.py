#!/usr/bin/env python3
"""恢复owned child到已设置坏SHA规则的代理。"""
import json
import backend_cases as h

SOURCE = r'''import hashlib,json,os,pwd,subprocess,time
from pathlib import Path
p=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";owner=json.loads(mp.read_bytes())
expected="14f3adb750b9021b87856e7794f3126cd71a0dc338c20b86719c89bb497b907c"
def sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()
def start(pid):
 raw=Path("/proc",str(pid),"stat").read_text();return int(raw[raw.rfind(")")+2:].split()[19])
def uid(pid):
 for row in Path("/proc",str(pid),"status").read_text().splitlines():
  if row.startswith("Uid:"):return int(row.split()[1])
 raise RuntimeError("uid_missing")
def argv(pid):return [x.decode(errors="replace") for x in Path("/proc",str(pid),"cmdline").read_bytes().split(bytes([0])) if x]
ds=p.lstat();raw=mp.read_bytes()
if (ds.st_dev,ds.st_ino,ds.st_uid)!=(33,135,65534) or sha(mp)!=expected or owner["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or owner["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5" or (p/"owner.marker").read_text()!=owner["owner_marker"]:raise RuntimeError("owned_manifest_guard_failed")
if sha(p/"ws_proxy.py")!="7573c838dc562af47c5903c6f72c1c3ab4b8a4c7f3a0f8ee3042f753ad51103d" or sha(p/"nodesteer-agent")!="d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add":raise RuntimeError("owned_binary_or_proxy_sha_guard_failed")
try:start(owner["child"]["pid"]);raise RuntimeError("old_child_pid_alive")
except FileNotFoundError:pass
controls=p/"controls.json";st=controls.lstat();cfg=Path(owner["config"]["path"]);cs=cfg.lstat()
if controls.is_symlink() or st.st_uid!=65534 or (cfg.is_symlink() or cs.st_uid!=65534 or cs.st_mode&0o777!=0o600):raise RuntimeError("private_file_guard_failed")
fault=json.loads(controls.read_text())
if fault!={"corrupt_script_sha":"92c0e242-0f67-4553-92e0-f7b8ffe675e4"}:raise RuntimeError("fault_target_guard_failed")
gid=pwd.getpwnam("nobody").pw_gid
def drop():os.setgroups([]);os.setgid(gid);os.setuid(65534)
with (p/"agent.log").open("ab") as log:proc=subprocess.Popen([str(p/"nodesteer-agent"),"--config",str(cfg)],cwd=str(p),stdin=subprocess.DEVNULL,stdout=log,stderr=log,close_fds=True,start_new_session=True,preexec_fn=drop,env={"PATH":"/usr/bin:/bin","HOME":str(p)})
time.sleep(1)
if proc.poll() is not None:raise RuntimeError("child_restart_exited")
child={"pid":proc.pid,"starttime":start(proc.pid),"uid":65534,"exe_path":os.readlink("/proc/"+str(proc.pid)+"/exe"),"exe_sha256":sha("/proc/"+str(proc.pid)+"/exe"),"argv_owner_path_match":any(a==str(p) or a.startswith(str(p)+os.sep) for a in argv(proc.pid))}
if child["exe_sha256"]!="d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add" or not child["argv_owner_path_match"]:raise RuntimeError("new_child_process_proof_failed")
owner["child"]=child
with mp.open("w") as f:json.dump(owner,f,separators=(",",":"))
os.chmod(mp,0o600);os.chown(mp,65534,65534)
print(json.dumps({"status":"PASS","node_id":owner["node_id"],"agent_id":owner["agent_id"],"child":child,"supervisor":owner["supervisor"],"proxy_port":owner["proxy"]["port"],"proxy_source_sha256":owner["proxy"]["source_sha256"],"owner_manifest_sha256":sha(mp),"secret_values_emitted":False},separators=(",",":")))
'''


def main():
    import ast
    ast.parse(SOURCE)
    result = h.create_native4_command('BE-SCRIPT-SHA-002-start-fault-child', SOURCE, timeout=25)
    if result.get('status') != 'SUCCESS' or not isinstance(result.get('output'), dict) or result['output'].get('status') != 'PASS':
        raise RuntimeError('owned_child_restart_failed')
    output = result['output']
    h.atomic(h.HERE / 'owned-process-ledger.json', output)
    h.checkpoint('BE-SCRIPT-SHA-002', 'fault-child-restarted', **output)
    print(json.dumps({'status':'PASS','child_pid':output['child']['pid'],'proxy_port':output['proxy_port'],'owner_manifest_sha256':output['owner_manifest_sha256']},ensure_ascii=False))


if __name__ == '__main__':
    main()
