import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch03')
B = BASE / 'pagination-final'
PLAN = json.loads((B / 'prepare-case-plan.json').read_text())
FIXTURES = PLAN['fixtures']
ORIGIN = 'http://192.168.100.209:18080'
SESSION = 'qawebsecure20261010final'
CMD = ['agent-browser', '--session', SESSION, '--allowed-domains', '192.168.100.209',
       '--args', '--unsafely-treat-insecure-origin-as-secure=' + ORIGIN]
created, deleted, case_results = [], [], []
failure = None


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def save(name, obj):
    (B / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def run(args, *, stdin=None, timeout=25):
    r = subprocess.run(CMD + [str(x) for x in args], input=stdin, capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode:
        # Never copy CLI stdout/stderr into an error artifact; network detail may contain secrets.
        raise RuntimeError('agent-browser command failed: ' + str(args[0]))
    return r.stdout


def ev(js):
    d = json.loads(run(['--json', 'eval', js]))['data']['result']
    return d


def ev_stdin(js):
    d = json.loads(run(['--json', 'eval', '--stdin'], stdin=js))['data']['result']
    return d


def api_nodes():
    return ev('''(async()=>{const t=localStorage.getItem("nodesteer_token"),r=await fetch("/api/nodes",{headers:{Authorization:"Bearer "+t}});let d=await r.json();let a=Array.isArray(d)?d:(d.items||d.nodes||[]);return {http:r.status,count:a.length,nodes:a.map(x=>({id:x.id,agent_id:x.agent_id,hostname:x.hostname,ip:x.ip,status:x.status,sync_status:x.sync_status}))}})()''')


def node_get(node_id):
    return ev('''(async()=>{const t=localStorage.getItem("nodesteer_token"),r=await fetch("/api/nodes/"+%s,{headers:{Authorization:"Bearer "+t}});let d={};try{d=await r.json()}catch{};return {http:r.status,id:d.id,agent_id:d.agent_id,hostname:d.hostname,ip:d.ip,status:d.status,sync_status:d.sync_status}})()''' % json.dumps(node_id))


def network_enrollment_posts():
    # Authorization, form fields and postData remain inside captured stdout and are never emitted.
    r = subprocess.run(CMD + ['--json', 'network', 'requests'], capture_output=True, text=True, timeout=20)
    if r.returncode:
        return []
    try:
        items = json.loads(r.stdout).get('data', {}).get('requests', [])
    except Exception:
        return []
    out = []
    for x in items:
        u = x.get('url', '')
        if isinstance(u, str) and u.split('?', 1)[0].endswith('/api/nodes/enrollment') and x.get('method') == 'POST':
            out.append({'request_id': x.get('requestId'), 'event_status': x.get('status')})
    return out


def response_meta(request_id):
    # The detail contains generated shell commands/registration token. Parse and discard it in-process.
    r = subprocess.run(CMD + ['--json', 'network', 'request', str(request_id)], capture_output=True, text=True, timeout=20)
    if r.returncode:
        return None
    try:
        d = json.loads(r.stdout).get('data', {})
        status = d.get('status')
        body = d.get('responseBody')
        if isinstance(body, str):
            body = json.loads(body)
        if not isinstance(body, dict):
            return {'status': status, 'node_id': None, 'agent_id': None, 'body_json': False}
        node_id, agent_id = body.get('node_id'), body.get('agent_id')
        # Do not retain any command or token field from the API response.
        return {'status': status, 'node_id': node_id, 'agent_id': agent_id,
                'body_json': True, 'command_fields_present': all(k in body for k in ('native', 'docker_run', 'docker_compose')),
                'ids_sha256': hashlib.sha256((str(node_id) + '\0' + str(agent_id)).encode()).hexdigest()}
    except Exception:
        return None


def persist(phase, gate):
    p = json.loads((B / 'progress.json').read_text())
    p.update({'phase': phase, 'updated_at_utc': now(), 'created_ids': [x['id'] for x in created],
              'deleted_ids': [x['id'] for x in deleted],
              'cleanup_verified': len(deleted) == len(created) and all(x['get_after_http'] == 404 for x in deleted),
              'results': case_results, 'current_gate': gate})
    (B / 'progress.json').write_text(json.dumps(p, ensure_ascii=False, indent=2) + '\n')
    ri = json.loads((B / 'resource-intents.json').read_text())
    ri.update({'updated_at_utc': now(), 'created': [{k: x.get(k) for k in ('id','agent_id','purpose','name','ip','post_status','request_id','status','sync_status','ids_sha256')} for x in created],
               'deleted': deleted,
               'cleanup_verified': len(deleted) == len(created) and all(x['get_after_http'] == 404 for x in deleted),
               'private_values_saved': False})
    (B / 'resource-intents.json').write_text(json.dumps(ri, ensure_ascii=False, indent=2) + '\n')


def record_case(case_id, status, details, evidence):
    result = {'captured_at_utc': now(), 'canonical_id': case_id, 'status': status,
              'details': details, 'evidence': evidence}
    case_results[:] = [x for x in case_results if x['canonical_id'] != case_id]
    case_results.append(result)
    save('case-' + case_id.lower() + '.json', result)
    persist('RUNNING', case_id + ' observed and written to disk')


def capture_page():
    return ev('''(()=>{const rows=[...document.querySelectorAll("main table tbody tr")];const hrefIds=rows.flatMap(r=>[...r.querySelectorAll('a[href^="/agents/"]')].map(a=>a.getAttribute('href').split('/').pop()));const sel=document.querySelector("main [role=combobox]");const m=document.querySelector("main")?.innerText||"";return {path:location.pathname,row_count:rows.length,href_ids:hrefIds,page_size:sel?.innerText.trim()||sel?.getAttribute("aria-valuetext")||null,visible_summary:m.slice(-220)}})()''')


def click_page_size(value):
    run(['find', 'role', 'combobox', 'click'])
    run(['find', 'role', 'option', 'click', '--name', str(value), '--exact'])
    run(['wait', '400'])


try:
    # Revalidate session and fixture isolation before any mutation.
    auth = ev('''(async()=>{const t=localStorage.getItem("nodesteer_token"),r=await fetch("/api/me",{headers:{Authorization:"Bearer "+t}});let d={};try{d=await r.json()}catch{};return {http:r.status,token_present:!!t,role:d.role||d.user?.role||null,online:navigator.onLine,secure:isSecureContext}})()''')
    token = ev('localStorage.getItem("nodesteer_token")')
    private = json.loads(Path('/tmp/nodesteer-e2e-resume-20261009-Qv9AYO/repair-connection.json').read_text())
    if auth['http'] != 200 or not auth['token_present'] or not auth['online'] or not auth['secure'] or token == private['token']:
        raise RuntimeError('exclusive authenticated online secure browser gate failed')
    baseline = api_nodes()
    if baseline['http'] != 200 or baseline['count'] != 6:
        raise RuntimeError('live baseline node count was not six; no fixture mutation attempted')
    existing_names = {x['hostname'].lower() for x in baseline['nodes']}
    existing_ips = {x['ip'].lower() for x in baseline['nodes']}
    if any(x['name'].lower() in existing_names or x['ip'].lower() in existing_ips for x in FIXTURES):
        raise RuntimeError('fixture collision; no fixture mutation attempted')
    persist('PREFLIGHT_COMPLETE', 'Authenticated private session, live baseline6, and five exact name/IP values absent.')
    run(['network', 'requests', '--clear'])

    for fixture in FIXTURES:
        run(['open', ORIGIN + '/agents'])
        run(['wait', '--load', 'networkidle'])
        run(['find', 'role', 'button', 'click', '--name', '添加节点'])
        # Real form controls are filled by agent-browser. No generated command is ever snapshotted.
        run(['find', 'label', '节点名称', 'fill', fixture['name']])
        run(['find', 'label', '节点地址（IP 或域名）', 'fill', fixture['ip']])
        run(['find', 'label', 'Hub 地址', 'fill', ORIGIN])
        actual = ev('''(()=>[...document.querySelectorAll('[role=dialog] input')].map(x=>x.value))()''')
        if actual != [fixture['name'], fixture['ip'], ORIGIN]:
            raise RuntimeError('UI form exact value check failed; submit withheld')
        run(['network', 'requests', '--clear'])
        run(['find', 'role', 'button', 'click', '--name', '生成安装命令'])
        request_id, meta = None, None
        deadline = time.monotonic() + 55
        while time.monotonic() < deadline:
            posts = network_enrollment_posts()
            if posts:
                # One POST was induced by this action; keep only its request ID/status metadata.
                candidate = posts[-1]
                if candidate.get('request_id'):
                    request_id = candidate['request_id']
                    meta = response_meta(request_id)
                    if meta and meta.get('status') is not None and meta.get('node_id') and meta.get('agent_id'):
                        break
            time.sleep(0.2)
        if not meta or meta.get('status') != 200 or not meta.get('node_id') or not meta.get('agent_id'):
            raise RuntimeError('POST did not expose final HTTP200 and node_id/agent_id within bounded wait; request tracked=' + str(bool(request_id)))
        # Persist actual POST response IDs before reading/asserting the corresponding Node record.
        owned = {'id': meta['node_id'], 'agent_id': meta['agent_id'], 'purpose': fixture['purpose'],
                 'name': fixture['name'], 'ip': fixture['ip'], 'post_status': meta['status'],
                 'request_id': request_id, 'ids_sha256': meta['ids_sha256'], 'status': None,
                 'sync_status': None, 'pending_detail_http': None, 'response_fields_ok': meta['command_fields_present']}
        created.append(owned)
        persist('POST_RESPONSE_ID_LEDGERED', fixture['purpose'] + ' node_id/agent_id from completed real enrollment POST durably recorded.')
        detail = node_get(owned['id'])
        owned.update({'pending_detail_http': detail.get('http'), 'status': detail.get('status'), 'sync_status': detail.get('sync_status')})
        persist('PENDING_GET_VERIFIED', fixture['purpose'] + ' GET /api/nodes/{returned-id} completed.')
        if detail['http'] != 200 or detail['id'] != owned['id'] or detail['agent_id'] != owned['agent_id'] or detail['hostname'] != owned['name'] or detail['ip'] != owned['ip'] or detail['status'] != 'pending':
            raise RuntimeError('pending node direct GET identity/status oracle mismatch after ledger')
        # Dialog includes secret-bearing generated commands; close it without reading or capturing contents.
        run(['press', 'Escape'])
        run(['wait', '300'])

    current = api_nodes()
    if current['count'] != 11:
        raise RuntimeError('API dataset count after five owned Nodes was not eleven')
    run(['open', ORIGIN + '/agents'])
    run(['wait', '--load', 'networkidle'])
    # Page size is a real control; inspect current option names only.
    options = ev('''(()=>[...document.querySelectorAll('[role=option]')].map(x=>x.innerText.trim()))()''')
    click_page_size(10)
    first = capture_page()
    run(['screenshot', str((B / 'p04-08-page1.png').resolve())])
    next_enabled = ev('''(()=>{const b=[...document.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='下一页');return !!b&&!b.disabled})()''')
    if next_enabled:
        run(['find', 'role', 'button', 'click', '--name', '下一页'])
        run(['wait', '350'])
    second = capture_page()
    run(['screenshot', str((B / 'p04-08-page2.png').resolve())])
    previous_enabled = ev('''(()=>{const b=[...document.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='上一页');return !!b&&!b.disabled})()''')
    if previous_enabled:
        run(['find', 'role', 'button', 'click', '--name', '上一页'])
        run(['wait', '350'])
    restored = capture_page()
    click_page_size(20)
    page20 = capture_page()
    next20 = ev('''(()=>{const b=[...document.querySelectorAll('button')].find(b=>(b.innerText||'').trim()==='下一页');return !!b&&!b.disabled})()''')
    click_page_size(10)
    reset10 = capture_page()
    api_after = api_nodes()
    api_ids = {x['id'] for x in api_after['nodes']}
    first_ids, second_ids = set(first['href_ids']), set(second['href_ids'])
    all_ui_ids = first_ids | second_ids
    owned_ids = {x['id'] for x in created}
    p04_checks = {
        'api_http_200_count_11': api_after['http'] == 200 and api_after['count'] == 11,
        'page1_exactly_10': first['row_count'] == 10,
        'next_enabled': next_enabled,
        'page2_exactly_1': second['row_count'] == 1,
        'previous_enabled': previous_enabled,
        'previous_restores_page1': restored['href_ids'] == first['href_ids'],
        'page_size20_shows_all_11': page20['row_count'] == 11,
        'next_disabled_at20': not next20,
        'reset10_shows10': reset10['row_count'] == 10,
        'two_page_ids_equal_api_dataset': all_ui_ids == api_ids,
        'all_owned_nodes_in_rendered_pages': owned_ids.issubset(all_ui_ids),
        'same_route': all(x['path'] == '/agents' for x in (first, second, restored, page20, reset10))
    }
    p04pass = all(p04_checks.values())
    save('p04-08-pagination-oracle.json', {'captured_at_utc': now(), 'actions': ['select size10','Next','Previous','select size20','select size10'],
         'fixture_post_count': len(created), 'api_count': api_after['count'], 'api_ids': sorted(api_ids),
         'page1': first, 'next_enabled': next_enabled, 'page2': second, 'previous_enabled': previous_enabled,
         'restored_page1': restored, 'page_size20': page20, 'next_enabled_at20': next20, 'reset_page_size10': reset10,
         'checks': p04_checks, 'evidence': ['p04-08-page1.png','p04-08-page2.png'], 'private_values_saved': False})
    record_case('P04-08', 'PASS' if p04pass else 'PARTIAL', {'checks': p04_checks, 'api_count': api_after['count'], 'page1_rows': first['row_count'],
                'page2_rows': second['row_count'], 'page20_rows': page20['row_count'], 'union_matches_api': all_ui_ids == api_ids},
                ['p04-08-pagination-oracle.json','p04-08-page1.png','p04-08-page2.png'])

    pending_checks = all(x['pending_detail_http'] == 200 and x['status'] == 'pending' for x in created)
    save('pending-node-details.json', {'captured_at_utc': now(), 'nodes': [{'id':x['id'],'agent_id':x['agent_id'],
         'pending_detail_http':x['pending_detail_http'],'status':x['status'],'sync_status':x['sync_status']} for x in created],
         'all_five_post_response_ids_ledgered': len(created) == 5, 'pending_checks_pass': pending_checks,
         'agent_hello_online_synced': 'NOT_RUN: starting an Agent was explicitly out of scope', 'private_values_saved':False})
    record_case('P05A-12', 'PARTIAL', {'real_UI_enrollment_POST_returned_node_and_agent_IDs': len(created) == 5,
                'direct_node_GET_pending': pending_checks, 'Agent_HELLO_reuse_online_synced':'NOT_RUN: no Agent started in authorized pagination-only round'},
                ['pending-node-details.json'])

except Exception as exc:
    failure = {'captured_at_utc': now(), 'error_type': type(exc).__name__, 'safe_message': str(exc)[:220],
               'created_ids': [x['id'] for x in created]}
    save('pagination-final-runner-error.json', failure)
    persist('RUNNER_ERROR_CLEANUP_REQUIRED', 'A bounded pagination step failed; only response-ledgered IDs are in cleanup set.')

finally:
    # Always delete only IDs returned by this run's completed POST and already persisted above.
    for item in list(created):
        try:
            before = node_get(item['id'])
            if before['http'] == 404:
                after_http = 404
                delete_http = None
            else:
                if before['http'] != 200 or before.get('id') != item['id'] or before.get('hostname') != item['name'] or before.get('ip') != item['ip']:
                    raise RuntimeError('exact cleanup ownership guard failed')
                result = ev('''(async()=>{const t=localStorage.getItem("nodesteer_token"),r=await fetch("/api/nodes/"+%s,{method:"DELETE",headers:{Authorization:"Bearer "+t}});return {status:r.status}})()''' % json.dumps(item['id']))
                delete_http = result['status']
                after_http = node_get(item['id'])['http']
            deleted_item = {'id':item['id'],'delete_http':delete_http,'get_after_http':after_http,
                            'verified': (delete_http == 200 or delete_http is None) and after_http == 404}
            deleted.append(deleted_item)
            persist('EXACT_CLEANUP_IN_PROGRESS', 'Exact response-ledgered Node DELETE followed by detail GET recorded.')
        except Exception as exc:
            save('pagination-cleanup-error-' + hashlib.sha256(item['id'].encode()).hexdigest()[:8] + '.json',
                 {'captured_at_utc':now(),'id':item['id'],'error_type':type(exc).__name__,'safe_message':str(exc)[:160],
                  'deleted_ids':[x['id'] for x in deleted]})

    try:
        live_final = api_nodes()
        baseline_ids = {x['id'] for x in baseline['nodes']} if 'baseline' in globals() else set()
        final_by_id = {x['id']:x for x in live_final['nodes']}
        baseline_final = [{'id':x['id'],'hostname':x['hostname'],'present':x['id'] in final_by_id,
                           'status':final_by_id.get(x['id'],{}).get('status'),
                           'sync_status':final_by_id.get(x['id'],{}).get('sync_status')} for x in (baseline['nodes'] if 'baseline' in globals() else [])]
        final = {'captured_at_utc':now(), 'created':[{'id':x['id'],'agent_id':x['agent_id'],'purpose':x['purpose'],'post_status':x['post_status']} for x in created],
                 'deleted':deleted, 'all_created_get404':len(deleted)==len(created) and all(x['verified'] for x in deleted),
                 'final_api_http':live_final['http'],'final_api_node_count':live_final['count'],
                 'baseline_nodes':baseline_final,'all_baseline_ids_present':len(baseline_final)==6 and all(x['present'] for x in baseline_final),
                 'baseline_online_synced':all(x['status']=='online' and x['sync_status']=='synced' for x in baseline_final),
                 'browser_online':ev('navigator.onLine'),'secure_context':ev('isSecureContext'),
                 'route':run(['get','url']).strip().replace(ORIGIN,''), 'runner_failure':failure}
        save('pagination-final-verification.json',final)
        persist('FINAL_CLEANUP_COMPLETE' if final['all_created_get404'] else 'CLEANUP_INCOMPLETE',
                'No further fixture creation; summary awaits safe verification/merge.')
    except Exception as exc:
        save('pagination-final-verification-error.json', {'captured_at_utc':now(),'error_type':type(exc).__name__,'safe_message':str(exc)[:180]})

print(json.dumps({'fixture_created':len(created),'fixture_deleted':len(deleted),
                  'cleanup_verified':len(deleted)==len(created) and all(x['verified'] for x in deleted),
                  'case_results':{x['canonical_id']:x['status'] for x in case_results},
                  'runner_failed':failure is not None},ensure_ascii=False),flush=True)
