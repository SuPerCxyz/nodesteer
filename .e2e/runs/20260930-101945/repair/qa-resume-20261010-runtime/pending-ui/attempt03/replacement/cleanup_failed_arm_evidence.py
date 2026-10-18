#!/usr/bin/env python3
"""Remove one Root-reviewed failed arm-evidence file after full owner revalidation."""
import argparse, hashlib, json, os, stat, subprocess
from pathlib import Path

ROOT = Path('/tmp/qa-resume-20261008-child-ciivjupc')
UNIT = 'nodesteer-agent-repair-20260930-101945-native-4.service'
CG = '/system.slice/' + UNIT
NODE = '15961ef7-81b4-43f3-80c8-1b6977ca2aea'
AGENT = 'agent-ab15a133-73cd-4025-8c97-f2ff37e000f5'
MANIFEST_SHA = 'c8142503fc467066265b67a1db641ec3274119961f65cdadcd92f2ba26a2e2ce'
CONFIG = ROOT / 'child.yaml'
CONFIG_SHA = '2d36aeb7f541573683c5458f35e8322ea7b84024f33d01541f720b144dbbab1c'
CHILD = (49873, 137980573, 65534, 'd55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add')
PROXY = (44932, 123677922, 65534, '078fc5e8711442c8051d8c515a3f57bc1d22dcdb90e9b384af0c50916125b531')
TARGET = ROOT / 'arm-evidence-5be5b250c113.json'
TARGET_SHA = '67aa6eef790d283b5ec60ef31e053e7f480efca4b1e65eac3611529bee90a07b'
TASK = '79bdf556-033c-46aa-88c9-ba62b5c880b1'
NONCE = '5be5b250c113'
CTRL_SHA = '44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a'

def need(ok, code):
    if not ok: raise RuntimeError(code)

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()

def proc(pid, ticks, uid, exe_sha, *argv_need):
    base=Path('/proc')/str(pid); raw=(base/'stat').read_text(); fields=raw[raw.rfind(')')+2:].split()
    argv=[x.decode(errors='replace') for x in (base/'cmdline').read_bytes().split(b'\0') if x]
    cg=(base/'cgroup').read_text().splitlines()
    return (int(fields[19])==ticks and os.stat(base).st_uid==uid and sha(base/'exe')==exe_sha
            and all(x in argv for x in argv_need) and '0::'+CG in cg)

def guarded():
    need(os.geteuid()==0,'cleanup_helper_not_root')
    rs=ROOT.lstat();need(not stat.S_ISLNK(rs.st_mode) and ROOT.resolve()==ROOT and (rs.st_dev,rs.st_ino,rs.st_uid,rs.st_gid,stat.S_IMODE(rs.st_mode))==(33,135,65534,65534,0o700),'root_identity_mismatch')
    mp=ROOT/'owner-manifest.json';ms=mp.lstat();need(stat.S_ISREG(ms.st_mode) and (ms.st_uid,ms.st_gid,stat.S_IMODE(ms.st_mode))==(65534,65534,0o600) and sha(mp)==MANIFEST_SHA,'manifest_identity_or_sha_mismatch')
    m=json.loads(mp.read_text());need((m.get('node_id'),m.get('agent_id'))==(NODE,AGENT) and (m.get('directory_dev'),m.get('directory_ino'),m.get('directory_uid'))==(33,135,65534),'manifest_schema_identity_mismatch')
    marker=ROOT/'owner.marker';st=marker.lstat();ov=m.get('owner_marker');need(stat.S_ISREG(st.st_mode) and st.st_uid==65534 and isinstance(ov,str) and bool(ov) and marker.read_text(encoding='utf-8')==ov,'owner_marker_mismatch')
    need(proc(*CHILD,str(CONFIG)) and proc(*PROXY,str(ROOT/'supervisor.py'),'127.69.212.143'),'owned_process_guard_failed')
    cs=CONFIG.lstat();need(stat.S_ISREG(cs.st_mode) and (cs.st_uid,cs.st_gid,stat.S_IMODE(cs.st_mode))==(65534,65534,0o600) and sha(CONFIG)==CONFIG_SHA,'config_guard_failed')
    unit=subprocess.run(['systemctl','show',UNIT,'-p','ActiveState','-p','MainPID','-p','ControlGroup'],capture_output=True,text=True,timeout=5,check=False)
    props=dict(x.split('=',1) for x in unit.stdout.splitlines() if '=' in x);need(unit.returncode==0 and props.get('ActiveState')=='active' and props.get('MainPID')=='32487' and props.get('ControlGroup')==CG,'parent_unit_guard_failed')
    ctrl=ROOT/'controls.json';st=ctrl.lstat();need(stat.S_ISREG(st.st_mode) and (st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))==(65534,65534,0o600) and ctrl.read_bytes()==b'{}' and sha(ctrl)==CTRL_SHA,'controls_not_empty_or_changed')
    for name in ('qa-pending-ui-watchdog.py','watchdog-ready-'+NONCE+'.json','watchdog-lock-'+NONCE+'.lock','watchdog-arm-'+NONCE+'.json','watchdog-result-'+NONCE+'.json'):
        q=ROOT/name;need(not q.exists() and not q.is_symlink(),'unexpected_watchdog_sidecar')
    ts=TARGET.lstat();need(stat.S_ISREG(ts.st_mode) and (ts.st_uid,ts.st_gid,stat.S_IMODE(ts.st_mode))==(65534,65534,0o600) and sha(TARGET)==TARGET_SHA,'exact_cleanup_target_guard_failed')
    fd=os.open(TARGET,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    try:
        fs=os.fstat(fd);raw=os.read(fd,65537);need((fs.st_dev,fs.st_ino,fs.st_uid,fs.st_gid,stat.S_IMODE(fs.st_mode))==(ts.st_dev,ts.st_ino,ts.st_uid,ts.st_gid,0o600),'target_changed_during_open')
    finally: os.close(fd)
    e=json.loads(raw);need(e.get('status')=='ARM_GUARD_FAILED' and e.get('safe_error')=='owner_manifest_exact_identity_mismatch' and e.get('task_id')==TASK and e.get('hold_nonce')==NONCE and e.get('node_id')==NODE and e.get('agent_id')==AGENT,'target_record_identity_mismatch')
    return {'root':rs,'manifest':ms,'target':ts,'controls_before':ctrl.read_bytes(),'target_sha256':TARGET_SHA,'parent_main_pid':props['MainPID']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--execute-cleanup',action='store_true');a=p.parse_args();s=guarded()
    if not a.execute_cleanup:
        print(json.dumps({'status':'DRY_RUN_READY','target_name':TARGET.name,'target_sha256':s['target_sha256'],'controls_empty':True,'parent_main_pid':s['parent_main_pid']}));return 0
    s2=guarded(); need(s2['target_sha256']==s['target_sha256'] and s2['controls_before']==s['controls_before']==b'{}' and (s2['target'].st_dev,s2['target'].st_ino,s2['target'].st_uid,s2['target'].st_gid)==(s['target'].st_dev,s['target'].st_ino,s['target'].st_uid,s['target'].st_gid),'cleanup_guard_changed_before_unlink')
    st=TARGET.lstat(); need(stat.S_ISREG(st.st_mode) and (st.st_dev,st.st_ino,st.st_uid,st.st_gid,stat.S_IMODE(st.st_mode))==(s2['target'].st_dev,s2['target'].st_ino,65534,65534,0o600) and sha(TARGET)==TARGET_SHA,'target_changed_before_unlink')
    TARGET.unlink();fd=os.open(ROOT,os.O_RDONLY|getattr(os,'O_DIRECTORY',0));os.fsync(fd);os.close(fd)
    need(not TARGET.exists() and not TARGET.is_symlink() and (ROOT/'controls.json').read_bytes()==s['controls_before']==b'{}','cleanup_postcondition_failed')
    print(json.dumps({'status':'EXACT_ARM_EVIDENCE_REMOVED','target_name':TARGET.name,'removed_sha256':s['target_sha256'],'controls_empty_after':True,'parent_main_pid':s['parent_main_pid']}));return 0

if __name__=='__main__':
    raise SystemExit(main())
