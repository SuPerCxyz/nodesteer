#!/usr/bin/env python3
"""对合法Remote控制组作不改变payload的请求/响应摘要核验。"""
import json
import uuid
import backend_cases as h

CHILD=h.CHILD
HISTORY_TASK='0c1f41b8-5c7e-4db7-9aec-0df7d6c0c005'
CONTROL_TASK='d97fad20-736a-4e08-b608-1dbec046f267'
EXPECTED_MANIFEST='9d4a505cd99fc6acd9ea342e2bbbc22e156ff2e89da163cbea92a9ca1c9abfff'


def set_source(expected, rule):
    return '''import hashlib,json,os,stat\nfrom pathlib import Path\np=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";m=json.loads(mp.read_bytes());c=p/"controls.json"\ndef sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()\nif (p.stat().st_dev,p.stat().st_ino,p.stat().st_uid)!=(33,135,65534) or sha(mp)!=%r or m["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or m["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5" or (p/"owner.marker").read_text()!=m["owner_marker"]:raise RuntimeError("owned_manifest_guard_failed")\nst=c.lstat()\nif c.is_symlink() or not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode)!=0o600 or st.st_uid!=65534:raise RuntimeError("controls_file_guard_failed")\ncurrent=json.loads(c.read_text())\nif current!={}:raise RuntimeError("unexpected_existing_controls")\nvalue=%r\nt=c.with_suffix(".json.tmp");t.write_text(json.dumps(value,separators=(",",":")));os.chmod(t,0o600);os.chown(t,65534,65534);t.replace(c)\nprint(json.dumps({"status":"PASS","rules_sha256":sha(c),"controls_uid":c.stat().st_uid,"controls_mode":"0o600","observe_only":True},separators=(",",":")))\n'''%(expected,rule)


def clear_source(expected, rule):
    return '''import hashlib,json,os,stat\nfrom pathlib import Path\np=Path("/tmp/qa-resume-20261008-child-ciivjupc");mp=p/"owner-manifest.json";m=json.loads(mp.read_bytes());c=p/"controls.json"\ndef sha(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()\nif (p.stat().st_dev,p.stat().st_ino,p.stat().st_uid)!=(33,135,65534) or sha(mp)!=%r or m["node_id"]!="15961ef7-81b4-43f3-80c8-1b6977ca2aea" or m["agent_id"]!="agent-ab15a133-73cd-4025-8c97-f2ff37e000f5":raise RuntimeError("owned_manifest_guard_failed")\ncurrent=json.loads(c.read_text())\nif current!={"remote_override":%r}:raise RuntimeError("observe_only_rule_guard_failed")\nt=c.with_suffix(".json.tmp");t.write_text("{}");os.chmod(t,0o600);os.chown(t,65534,65534);t.replace(c)\nprint(json.dumps({"status":"PASS","controls_empty":True,"controls_sha256":sha(c)},separators=(",",":")))\n'''%(expected,rule)


def main():
    rule={'target_node_id':CHILD,'property':'last_execution','task_id':HISTORY_TASK,'observe_only':True}
    arm=h.create_native4_command('BE-REMOTE-TTL-observe-valid-response-arm',set_source(EXPECTED_MANIFEST,{'remote_override':rule}),timeout=20)
    if arm.get('status')!='SUCCESS' or arm.get('output',{}).get('status')!='PASS':raise RuntimeError('observe_only_remote_rule_arm_failed')
    run_error=None
    try:
        task=h.api_get('/api/tasks/'+h.url(CONTROL_TASK))
        result=h.run_task(task,'BE-REMOTE-TTL-valid-control-observed-run',expected_status='SUCCESS')
    except RuntimeError as e:
        run_error=str(e)
        data=json.loads((h.HERE/'BE-REMOTE-TTL-valid-control-observed-run-execution.json').read_text())
        result=data
    events_source='''import json\nfrom pathlib import Path\nrows=[]\nfor l in Path("/tmp/qa-resume-20261008-child-ciivjupc/proxy-events.jsonl").read_text().splitlines()[-500:]:\n try:\n  x=json.loads(l)\n  if x.get("event") in {"remote_request","remote_override"}:rows.append(x)\n  elif x.get("event")=="forward_control" and x.get("type")=="REMOTE_STATE":rows.append({k:x.get(k) for k in ["time","event","connection","direction","type","id"] if k in x})\n except Exception:pass\nprint(json.dumps(rows[-25:],separators=(",",":")))\n'''
    events=h.create_native4_command('BE-REMOTE-TTL-observe-valid-response-events',events_source,timeout=25)
    clear=h.create_native4_command('BE-REMOTE-TTL-observe-valid-response-clear',clear_source(EXPECTED_MANIFEST,rule),timeout=20)
    if clear.get('status')!='SUCCESS' or clear.get('output',{}).get('status')!='PASS':raise RuntimeError('observe_only_remote_rule_clear_failed')
    history=h.api_get('/api/executions/7071bbea-47b9-455e-b4c6-a78f36aa9537')
    h.atomic(h.HERE/'remote-valid-control-diagnostic.json',{'history_execution':{'id':history.get('id'),'node_id':history.get('node_id'),'task_id':history.get('task_id'),'status':history.get('status'),'synced':history.get('synced')},'condition_execution':result,'condition_error':run_error,'proxy_events':events.get('output'),'rule_was_observe_only':True,'rule_clear':clear.get('output')})
    print(json.dumps({'status':'RECORDED','condition_error':run_error,'condition_status':result.get('status') if isinstance(result,dict) else None,'proxy_events':events.get('output')},ensure_ascii=False)[:2500])


if __name__=='__main__':main()
