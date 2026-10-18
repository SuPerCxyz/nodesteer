#!/usr/bin/env python3
"""Read one Native1 cached Application URL via its live Agent-owned SQLite DB."""
import hashlib, json, os, re, sqlite3, stat, subprocess
from pathlib import Path
from urllib.parse import urlsplit

NODE="91f44866-8a28-435e-bbda-2f2367126c00"
AGENT="agent-d21d5e7d-0ff0-4355-b8de-4889dc865343"
APP="804e6b14-eb84-4dda-90de-8bcab9586c4c"
SHA="d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add"
UNIT="nodesteer-agent-repair-20260930-101945-native-1.service"
HOST, PORT = "192.168.100.209", 18080

class GuardError(Exception): pass

def proc(pid):
    p=Path("/proc")/str(pid); raw=(p/"stat").read_text(); i=raw.rfind(")")
    if i<0: raise GuardError("proc_stat_shape")
    tail=raw[i+2:].split(); uidline=next((x for x in (p/"status").read_text().splitlines() if x.startswith("Uid:")),"")
    if len(tail)<20 or not uidline: raise GuardError("proc_identity_missing")
    return {"pid":pid,"ppid":int(tail[1]),"ticks":int(tail[19]),"state":tail[0],"uid":int(uidline.split()[1]),"exe":os.readlink(p/"exe"),"cgroup":(p/"cgroup").read_text()}

def sha_fd(fd):
    h=hashlib.sha256(); os.lseek(fd,0,os.SEEK_SET)
    for b in iter(lambda:os.read(fd,1048576),b""): h.update(b)
    return h.hexdigest()

def agent_owner():
    pid=os.getppid()
    for _ in range(32):
        if pid<=1: break
        try:
            x=proc(pid); fd=os.open(f"/proc/{pid}/exe",os.O_RDONLY)
            try: procsha=sha_fd(fd)
            finally: os.close(fd)
        except (OSError,ValueError,IndexError): break
        if procsha==SHA and Path(x["exe"].removesuffix(" (deleted)")).name=="nodesteer-agent":
            if x["uid"]!=0 or x["state"]=="Z" or UNIT not in x["cgroup"]: raise GuardError("agent_proc_owner_unit_mismatch")
            fd=os.open(x["exe"].removesuffix(" (deleted)"),os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
            try:
                st=os.fstat(fd)
                if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or sha_fd(fd)!=SHA: raise GuardError("agent_disk_sha_owner_mismatch")
                disk={"dev":st.st_dev,"inode":st.st_ino}
            finally: os.close(fd)
            cp=subprocess.run(["systemctl","show",UNIT,"--no-pager","--property=Id,ActiveState,MainPID,ControlGroup"],capture_output=True,text=True,timeout=5,check=False)
            props=dict(line.split("=",1) for line in cp.stdout.splitlines() if "=" in line)
            if cp.returncode or props.get("Id")!=UNIT or props.get("ActiveState")!="active" or props.get("MainPID")!=str(pid) or props.get("ControlGroup") not in x["cgroup"]: raise GuardError("agent_unit_readback_mismatch")
            return x,disk,procsha
        pid=x["ppid"]
    raise GuardError("repair_agent_not_in_task_ancestors")

def state_db(pid):
    found={}
    for n in os.listdir(f"/proc/{pid}/fd"):
        if not n.isdigit(): continue
        q=Path(f"/proc/{pid}/fd/{n}")
        try: target,st=os.readlink(q),os.stat(q)
        except OSError: continue
        if target.endswith("/state.db") and stat.S_ISREG(st.st_mode): found[(st.st_dev,st.st_ino)]=target
    if len(found)!=1: raise GuardError("agent_state_db_fd_missing_or_ambiguous")
    (dev,ino),target=next(iter(found.items()))
    if target.endswith(" (deleted)") or not target.startswith("/"): raise GuardError("state_db_path_invalid")
    path=Path(target); fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or (st.st_dev,st.st_ino,st.st_uid)!=(dev,ino,0): raise GuardError("state_db_fd_path_mismatch")
    finally: os.close(fd)
    return path,dev,ino

def main():
    x,disk,procsha=agent_owner(); db,dev,ino=state_db(x["pid"])
    con=sqlite3.connect(db.as_uri()+"?mode=ro",uri=True,timeout=3)
    try:
        con.execute("PRAGMA query_only=ON"); quick=con.execute("PRAGMA quick_check").fetchone()[0]
        ident=dict(con.execute("SELECT key,value FROM identity WHERE key IN ('node_id','agent_id')"))
        rev=con.execute("SELECT global_revision FROM sync_state WHERE id=1").fetchone()[0]
        rows=con.execute("SELECT definition_json FROM applications WHERE id=?",(APP,)).fetchall()
        if quick!="ok" or ident!={"node_id":NODE,"agent_id":AGENT}: raise GuardError("db_health_or_identity_mismatch")
        if len(rows)>1: raise GuardError("exact_app_row_not_unique")
        app=json.loads(rows[0][0]) if rows else None
        if app is not None and (not isinstance(app,dict) or app.get("id") != APP): raise GuardError("app_definition_identity_mismatch")
        url=app.get("artifact_url") if app else None
        if not isinstance(url,str) or not url: url=None
    finally: con.close()
    after=proc(x["pid"])
    if (after["ticks"],after["uid"],after["cgroup"])!=(x["ticks"],0,x["cgroup"]): raise GuardError("agent_identity_changed_during_read")
    strict=False; pathclass="absent" if url is None else "other"; urlhash=hashlib.sha256(url.encode()).hexdigest() if url else None
    if url:
        try:
            u=urlsplit(url); strict=(u.scheme=="http" and u.hostname==HOST and u.port==PORT and not u.username and not u.password and not u.query and not u.fragment)
            if re.fullmatch(r"/api/artifacts/[A-Za-z0-9-]+/download",u.path): pathclass="artifact-download"
        except ValueError: pathclass="malformed"
    print(json.dumps({"status":"NATIVE1_APP_ARTIFACT_URL_READONLY","node_id":NODE,"agent_id":AGENT,"app_id":APP,"application_row_count":len(rows),"agent_pid":x["pid"],"agent_start_ticks":x["ticks"],"agent_uid":x["uid"],"agent_proc_exe_sha256":procsha,"agent_disk_sha256":SHA,"agent_disk_dev":disk["dev"],"agent_disk_inode":disk["inode"],"unit":UNIT,"state_db_path_class":"var-lib-nodesteer" if str(db).startswith("/var/lib/nodesteer/") else "other","state_db_dev":dev,"state_db_inode":ino,"quick_check":quick,"local_global_revision":rev,"artifact_url_present":url is not None,"artifact_url_sha256":urlhash,"artifact_url_origin_strict_repair_18080":strict,"artifact_url_path_class":pathclass,"raw_url_emitted":False,"config_env_or_definition_emitted":False},sort_keys=True,separators=(",",":")),flush=True)

if __name__=="__main__":
    try: main()
    except GuardError as e: print(json.dumps({"status":"SAFE_STOP","safe_error":str(e),"raw_url_emitted":False,"config_env_or_definition_emitted":False},sort_keys=True),flush=True); raise SystemExit(2)
    except Exception as e: print(json.dumps({"status":"SAFE_STOP","safe_error_type":type(e).__name__,"raw_url_emitted":False,"config_env_or_definition_emitted":False},sort_keys=True),flush=True); raise SystemExit(2)
