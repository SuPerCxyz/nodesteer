#!/usr/bin/env python3
"""仅Native3新增Task和own /tmp，当前生产路径的SYS34/40/53真实补测。"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time
import traceback
import urllib.error
import urllib.request
import uuid

RUN_DIR = Path(__file__).resolve().parent
ROOT = RUN_DIR.parent.parent
NODE_ID = '811d72ef-75f8-47ba-84de-8cb1e6974315'
PREFIX = 'qa-20260930-101945-backend-extra'
TERMINAL = {'SUCCESS', 'FAILED', 'BLOCKED', 'SKIPPED', 'TIMED_OUT', 'CANCELED'}


class Runner:
    def __init__(self, connection):
        self.cfg = json.loads(connection.read_text())
        assert connection.stat().st_mode & 0o077 == 0
        self.base = self.cfg['base_url'].rstrip('/')
        self.node = next(n for n in self.cfg['nodes'] if n['id'] == NODE_ID)
        self.token = ''
        self.secrets = [str(v) for k, v in self.cfg.items() if any(x in k for x in ['password', 'token']) and isinstance(v, str)]
        self.events = []
        self.ledger = json.loads((RUN_DIR / 'ledger.json').read_text()) if (RUN_DIR / 'ledger.json').exists() else []
        self.directory = '/tmp/' + PREFIX + '-' + uuid.uuid4().hex[:8]
        self.results = []
        value = self.api('POST', '/api/login', {'username': self.cfg['admin_username'], 'password': self.cfg['admin_password']})
        self.token = value['token']; self.secrets.append(self.token)
        me = self.api('GET', '/api/me'); assert me['Role'] == 'administrator'
        node = self.api('GET', '/api/nodes/' + NODE_ID)
        assert node['status'] == 'online' and node['sync_status'] == 'synced' and node['deployment_mode'] == 'native'
        self.baseline = self.remote_json('import subprocess,json,os,pwd,grp\npid=int(subprocess.check_output(["systemctl","show","nodesteer-agent.service","-p","MainPID","--value"],text=True));u=pwd.getpwnam("nobody");print(json.dumps({"agent_pid":pid,"agent_active":subprocess.check_output(["systemctl","is-active","nodesteer-agent.service"],text=True).strip(),"nobody_uid":u.pw_uid,"nobody_gid":u.pw_gid,"nobody_group":grp.getgrgid(u.pw_gid).gr_name}))')
        self.save(RUN_DIR / ('baseline-' + self.directory.rsplit('-', 1)[1] + '.json'), {'node_id': NODE_ID, 'own_directory': self.directory, 'baseline': self.baseline})

    def safe(self, value):
        if isinstance(value, dict): return {k: self.safe(v) for k, v in value.items()}
        if isinstance(value, list): return [self.safe(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                if secret: value = value.replace(secret, '[REDACTED]')
        return value

    def save(self, path, value):
        path.write_text(json.dumps(self.safe(value), ensure_ascii=False, indent=2) + '\n')

    def api(self, method, path, body=None, expected=(200,), record=True):
        headers = {'Content-Type': 'application/json'}
        if self.token: headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.base + path, method=method, headers=headers, data=json.dumps(body).encode() if body is not None else None)
        try: response = urllib.request.urlopen(request, timeout=25)
        except urllib.error.HTTPError as error: response = error
        with response: status, payload = response.status, response.read()
        value = json.loads(payload) if payload else None
        if record: self.events.append({'api': method + ' ' + path, 'http_status': status, 'request': body, 'response': value})
        assert status in expected, self.safe(str({'api': method + ' ' + path, 'http_status': status, 'response': value}))
        return value

    def remote(self, shell):
        argv = self.node['executor_argv']
        assert NODE_ID in argv, '客户机helper必须精确指向授权Native3'
        result = subprocess.run(argv, input=shell + '\n', text=True, capture_output=True, timeout=40)
        assert result.returncode == 0, 'Native3 helper读取/own fixture操作失败: ' + self.safe(result.stderr[-400:])
        return result.stdout.strip()

    def remote_json(self, code):
        return json.loads(self.remote('python3 - <<\'PY\'\n' + code + '\nPY'))

    def task(self, label, code, retry=0, run_user=''):
        task = self.api('POST', '/api/tasks', {'name': PREFIX + '-' + label + '-' + uuid.uuid4().hex[:6], 'type': 'command', 'target': {'type': 'node', 'node_ids': [NODE_ID]}, 'command': 'python3 -c ' + shlex.quote(code), 'timeout': 25, 'retry': retry, 'run_user': run_user, 'offline_policy': 'hub_online_required', 'enabled': True})
        self.ledger.append({'type': 'task', 'id': task['id'], 'name': task['name'], 'status': 'created'})
        self.save(RUN_DIR / 'ledger.json', self.ledger)
        return task

    def journal(self, eid):
        code = 'import sqlite3,json\nc=sqlite3.connect(' + repr('file:' + self.node['data_dir'] + '/state.db?mode=ro') + ',uri=True);c.row_factory=sqlite3.Row;row=c.execute("select id,task_id,node_id,status,synced,offline from executions where id=?",[' + repr(eid) + ']).fetchone();print(json.dumps(dict(row) if row else None))'
        return self.remote_json(code)

    def run(self, task, expected='SUCCESS'):
        values = self.api('POST', '/api/tasks/' + task['id'] + '/run', {})
        assert len(values) == 1 and values[0]['node_id'] == NODE_ID
        eid = values[0]['id']; self.ledger.append({'type': 'execution', 'id': eid, 'task_id': task['id'], 'status': 'retain-history'})
        self.save(RUN_DIR / 'ledger.json', self.ledger)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            final = self.api('GET', '/api/executions/' + eid, record=False)
            if final['status'] in TERMINAL: break
            time.sleep(.3)
        assert final['status'] == expected, self.safe(str(final))
        for _ in range(12):
            journal = self.journal(eid)
            if journal and journal['synced']: break
            time.sleep(.3)
        assert journal and journal['status'] == expected and journal['synced']
        self.events.append({'terminal_execution': final, 'agent_journal_readonly': journal})
        return final

    def chunks(self, execution):
        chunks = self.api('GET', '/api/executions/' + execution['id'] + '/logs')
        assert chunks
        assert [c['seq'] for c in chunks] == list(range(len(chunks))), 'seq必须从0连续、单调且不重复'
        assert all(c['execution_id'] == execution['id'] and c['stream'] in ['stdout', 'stderr'] for c in chunks)
        return chunks

    def case34(self):
        prefix = 'QA34-' + uuid.uuid4().hex[:6]
        serial = 'import sys,time\n' + '\n'.join('sys.' + stream + '.write(' + repr(prefix + '|' + marker + '\n') + ');sys.' + stream + '.flush();time.sleep(.18)' for marker, stream in [('O0','stdout'),('E0','stderr'),('O1','stdout'),('E1','stderr'),('O2','stdout'),('E2','stderr')])
        effect = self.directory + '/sys34-serial.json'
        serial += '\nimport os,json;os.makedirs(' + repr(self.directory) + ',exist_ok=True);open(' + repr(effect) + ',"w").write(json.dumps({"serial_writes":6}))'
        execution = self.run(self.task('sys34-serial', serial)); chunks = self.chunks(execution)
        seen = []
        for chunk in chunks:
            for line in chunk['chunk'].splitlines():
                assert line.startswith(prefix + '|')
                marker = line.split('|')[1]; assert chunk['stream'] == ('stdout' if marker.startswith('O') else 'stderr')
                seen.append({'marker': marker, 'seq': chunk['seq'], 'stream': chunk['stream']})
        assert [s['marker'] for s in seen] == ['O0','E0','O1','E1','O2','E2']
        assert self.remote_json('import json;print(open(' + repr(effect) + ').read())')['serial_writes'] == 6
        concurrent = 'import sys,time,threading,json,os\nprefix=' + repr(prefix) + '\ndef write(stream,mark):\n for i in range(30):\n  stream.write(prefix+"|"+mark+str(i)+"\\n");stream.flush();time.sleep(.003)\na=threading.Thread(target=write,args=(sys.stdout,"O"));b=threading.Thread(target=write,args=(sys.stderr,"E"));a.start();b.start();a.join();b.join();open(' + repr(self.directory + '/sys34-concurrent.json') + ',"w").write(json.dumps({"stdout_lines":30,"stderr_lines":30}))'
        second = self.run(self.task('sys34-concurrent', concurrent)); burst = self.chunks(second)
        stream_text = {'stdout': '', 'stderr': ''}
        for chunk in burst:
            # 先逐chunk验证seq/stream，再按各stream跨chunk边界解析完整行。
            stream_text[chunk['stream']] += chunk['chunk']
        for stream, marker in [('stdout','O'),('stderr','E')]:
            lines = stream_text[stream].splitlines()
            assert lines == [prefix + '|' + marker + str(i) for i in range(30)]
            assert second[stream] == stream_text[stream]
        actual = self.remote_json('import json;print(open(' + repr(self.directory + '/sys34-concurrent.json') + ').read())')
        assert actual == {'stdout_lines':30,'stderr_lines':30}
        self.events.append({'sys34_actual': {'serial_per_chunk_events':seen,'serial_chunks':chunks,'concurrent_chunks':burst,'concurrent_per_stream_line_count':actual,'concurrent_cross_stream_order':'不对并发线程臆造固定交错次序；同stream顺序与全局seq均实测'}})

    def case40(self):
        counter = self.directory + '/sys40-attempts.jsonl'
        code = 'import os,json,sys,time\nos.makedirs(' + repr(self.directory) + ',exist_ok=True);p=' + repr(counter) + '\nrows=[json.loads(x) for x in open(p)] if os.path.exists(p) else [];n=len(rows)+1;start=time.time_ns();row={"attempt":n,"started_ns":start};open(p,"a").write(json.dumps(row)+"\\n");print("QA40|O|"+str(n)+"|"+str(start),flush=True);print("QA40|E|"+str(n),file=sys.stderr,flush=True);time.sleep(.15);end=time.time_ns();print("QA40|END|"+str(n)+"|"+str(end),flush=True);sys.exit(7 if n<3 else 0)'
        task = self.task('sys40-retry', code, retry=2); execution = self.run(task); chunks = self.chunks(execution)
        texts = {stream: ''.join(c['chunk'] for c in chunks if c['stream']==stream) for stream in ['stdout','stderr']}
        stdout_lines=texts['stdout'].splitlines(); stderr_lines=texts['stderr'].splitlines()
        starts=[line.split('|') for line in stdout_lines if line.startswith('QA40|O|')];ends=[line.split('|') for line in stdout_lines if line.startswith('QA40|END|')]
        assert [int(x[2]) for x in starts] == [1,2,3] and [int(x[2]) for x in ends] == [1,2,3]
        assert stderr_lines == ['QA40|E|1','QA40|E|2','QA40|E|3']
        fixture = self.remote_json('import json;print(json.dumps([json.loads(x) for x in open(' + repr(counter) + ')]))')
        assert [x['attempt'] for x in fixture] == [1,2,3]
        intervals=[(int(starts[i][3])-int(ends[i-1][3]))/1e9 for i in [1,2]]
        assert all(x>=0 for x in intervals)
        audits = self.api('GET', '/api/audit?limit=500')
        own=[x for x in audits if x.get('resource_id')==task['id']]
        runs=[x for x in own if x['action']=='execute']
        assert len(runs)==1 and runs[0]['resource']=='task'
        journal_logs=self.remote_json('import subprocess,json\np=subprocess.run(["journalctl","-u","nodesteer-agent.service","--since","10 minutes ago","--no-pager","-o","cat"],capture_output=True,text=True);print(json.dumps([x for x in p.stdout.splitlines() if ' + repr(execution['id']) + ' in x and "execution retry" in x]))')
        assert len(journal_logs)==2, '两次实际retry Agent日志未完整观察'
        assert 'QA40|O|3|' in execution['stdout'] and 'QA40|O|1|' not in execution['stdout']
        self.events.append({'sys40_actual':{'execution_id':execution['id'],'attempts':fixture,'all_attempt_chunks':chunks,'gap_after_previous_attempt_sec':intervals,'interval_contract':'当前无retry interval字段或等待；只观察实际进程间隙，不新增固定延迟承诺','hub_execute_audits':runs,'agent_retry_journal':journal_logs,'terminal_output_contract':'终态stdout/stderr来自最后attempt，/logs真实包含3次attempt'}})

    def case53(self):
        uid,gid=self.baseline['nobody_uid'],self.baseline['nobody_gid'];sub=self.directory+'/sys53';source=sub+'/source';target=sub+'/received'
        prepare='import os,json\nos.makedirs('+repr(sub)+',exist_ok=True);os.chown('+repr(sub)+','+str(uid)+','+str(gid)+');os.chmod('+repr(sub)+',0o770);open('+repr(source)+',"wb").write(b"QA53-relay-bytes");os.chmod('+repr(source)+',0o644);print(json.dumps({"fixture_directory_mode":oct(os.stat('+repr(sub)+').st_mode&0o777)}))'
        self.run(self.task('sys53-fixture',prepare))
        file=sub+'/nobody-file';output=sub+'/nobody-state.json'
        code='import os,json,stat\np='+repr(file)+';fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640);os.write(fd,b"QA53-nobody");os.close(fd);os.chmod(p,0o640);s=os.stat(p);result={"uid":os.getuid(),"gid":os.getgid(),"groups":os.getgroups(),"file_uid":s.st_uid,"file_gid":s.st_gid,"file_mode":oct(stat.S_IMODE(s.st_mode))};os.chmod(p,0o400)\ntry:\n open(p,"w").write("forbidden");result["write_denied"]=False\nexcept PermissionError:result["write_denied"]=True\nos.chmod(p,0o640);open('+repr(output)+',"w").write(json.dumps(result));print(json.dumps(result))'
        execution=self.run(self.task('sys53-run-user',code,run_user='nobody'));state=json.loads(execution['stdout'])
        assert state['uid']==state['file_uid']==uid and state['gid']==state['file_gid']==gid and state['file_mode']=='0o640' and state['write_denied']
        assert self.remote_json('import json;print(open('+repr(output)+').read())')==state
        transfer=self.api('POST','/api/transfers',{'source_node_id':NODE_ID,'source_path':source,'targets':[{'node_id':NODE_ID,'destination_path':target,'mode':0o640}]})
        self.ledger.append({'type':'transfer','id':transfer['id'],'status':'retain-history','source_path':source,'destination_path':target});self.save(RUN_DIR/'ledger.json',self.ledger)
        for _ in range(70):
            value=self.api('GET','/api/transfers/'+transfer['id'],record=False)
            if value['status'] in ['SUCCESS','FAILED','CANCELED']:break
            time.sleep(.3)
        assert value['status']=='SUCCESS'
        actual=self.remote_json('import os,json,hashlib,stat,pathlib\np='+repr(target)+';s=os.stat(p);print(json.dumps({"mode":oct(stat.S_IMODE(s.st_mode)),"uid":s.st_uid,"gid":s.st_gid,"sha256":hashlib.sha256(open(p,"rb").read()).hexdigest(),"bytes":os.path.getsize(p),"temp_files":[x.name for x in pathlib.Path('+repr(sub)+').glob(".nodesteer-transfer-*")]}))')
        assert actual['mode']=='0o640' and actual['sha256']==hashlib.sha256(b'QA53-relay-bytes').hexdigest()==value['sha256'] and not actual['temp_files']
        self.api('POST','/api/transfers/'+transfer['id']+'/cancel',{})
        self.events.append({'sys53_actual':{'run_user_execution':execution['id'],'run_user_effect':state,'native_relay_atomic_mode':actual,'relay_before_cleanup':value,'applicable_paths':['Task.RunUser→execution.Runner.Credential uid/gid→own文件真实权限','FileRelay.Mode→NativeHostAdapter.AtomicReplaceReader/Chmod→own目标0640/SHA/无tmp'],'chown_contract':'HostAdapter.Chown无任何生产调用，NOT_APPLICABLE；fixture os.chown只是设置测试目录，不冒称adapter方法被调用'}})

    def execute(self, cid):
        self.events=[];result={'case_id':cid,'started_at':dt.datetime.now(dt.timezone.utc).isoformat(),'node_id':NODE_ID,'classification':'REAL_API_AGENT_GUEST','status':'running'}
        try:getattr(self,'case'+cid.split('-')[1])();result['status']='PASS'
        except Exception as exc:result.update(status='FAIL_OR_HARNESS',reason=self.safe(str(exc)),traceback=self.safe(traceback.format_exc()))
        result['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();name=cid+'-'+uuid.uuid4().hex[:6]+'.json';result['evidence']='backend/continuation/'+name;self.save(RUN_DIR/name,{'result':result,'events':self.events});self.results.append(result);self.save(RUN_DIR/'progress.json',{'results':self.results,'ledger':'backend/continuation/ledger.json'});print(cid,result['status'],result.get('reason',''),flush=True)

    def cleanup(self):
        self.events=[]
        for row in self.ledger:
            if row['type']=='task' and row['status']=='created':
                self.api('DELETE','/api/tasks/'+row['id']);self.api('GET','/api/tasks/'+row['id'],expected=(404,));row['status']='deleted';self.save(RUN_DIR/'ledger.json',self.ledger)
            elif row['type']=='transfer':self.api('POST','/api/transfers/'+row['id']+'/cancel',{})
        assert self.directory.startswith('/tmp/'+PREFIX+'-')
        self.remote('python3 - <<\'PY\'\nimport pathlib,shutil\np=pathlib.Path('+repr(self.directory)+');shutil.rmtree(p) if p.exists() else None;assert not p.exists()\nPY')
        after=self.remote_json('import subprocess,json\nprint(json.dumps({"agent_pid":int(subprocess.check_output(["systemctl","show","nodesteer-agent.service","-p","MainPID","--value"],text=True)),"agent_active":subprocess.check_output(["systemctl","is-active","nodesteer-agent.service"],text=True).strip()}))')
        assert after['agent_pid']==self.baseline['agent_pid'] and after['agent_active']=='active'
        self.save(RUN_DIR/'cleanup.json',{'exact_ledger':self.ledger,'own_directory_removed':self.directory,'before_agent':self.baseline,'after_agent':after,'daemon_not_restarted':True,'agent_db_cache_direct_mutations':0,'events':self.events})
        self.api('POST','/api/logout',{});print('exact cleanup PASS; only own fresh session logout; no daemon/DB/cache/config mutations',flush=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('--connection',type=Path,required=True);p.add_argument('--case',action='append',required=True);a=p.parse_args();r=Runner(a.connection)
    try:
        for cid in a.case:r.execute(cid)
    finally:r.cleanup()
    r.save(RUN_DIR/'summary.json',{'run_id':'20260930-101945','results':r.results,'SYS-27':'NOT_RUN: 无安全可操作的物理独立offline peer，不用pending/disabled DB状态冒充','cleanup':'backend/continuation/cleanup.json'})
    raise SystemExit(1 if any(x['status']!='PASS' for x in r.results) else 0)


if __name__=='__main__':main()
