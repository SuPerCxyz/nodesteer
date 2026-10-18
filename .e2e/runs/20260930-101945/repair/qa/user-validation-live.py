#!/usr/bin/env python3
"""Real repair Hub user-input validation with safe retained Viewer fixture."""
import json, os, secrets, urllib.error, urllib.request
from pathlib import Path
RUN='20260930-101945'
PRIVATE=Path('/tmp/nodesteer-e2e-'+RUN+'-repair')
conn=json.loads((PRIVATE/'repair-connection.json').read_text())
base=conn['hub_url'].rstrip('/')
token=conn['token']

def req(method,path,body=None,bearer=True):
    data=None if body is None else json.dumps(body).encode()
    headers={'Content-Type':'application/json'} if data is not None else {}
    if bearer and token: headers['Authorization']='Bearer '+token
    q=urllib.request.Request(base+path,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(q,timeout=15) as r: code=r.status; raw=r.read()
    except urllib.error.HTTPError as e: code=e.code; raw=e.read()
    try: payload=json.loads(raw) if raw else None
    except (ValueError,UnicodeDecodeError): payload=None
    return code,payload

suffix=secrets.token_hex(4)
username='qa-repair-'+RUN+'-validation-'+suffix
pw='QaValid-'+secrets.token_urlsafe(18)
blank_create,p1=req('POST','/api/users',{'username':username+'-blank','password':'','role':'viewer'})
assert blank_create in (400,422),f'empty create password accepted: HTTP {blank_create}'
create_code,user=req('POST','/api/users',{'username':username,'password':pw,'role':'viewer'})
assert create_code in (200,201) and isinstance(user,dict),f'valid fixture create failed: HTTP {create_code}'
uid=user.get('id') or user.get('ID')
assert uid, 'new QA user response lacks ID'
invalid_code,invalid=req('PUT','/api/users/'+uid,{'role':'qa_invalid_role'})
assert invalid_code in (400,422),f'invalid role accepted: HTTP {invalid_code}'
users_code,users=req('GET','/api/users')
assert users_code==200 and isinstance(users,list),'admin user list unavailable'
row=next((x for x in users if (x.get('id') or x.get('ID'))==uid),None)
role=(row or {}).get('role',(row or {}).get('Role'))
assert role=='viewer',f'invalid role mutation persisted: {role}'
empty_pw_code,empty_pw=req('PUT','/api/users/'+uid,{'password':''})
assert empty_pw_code in (400,422),f'empty update password accepted: HTTP {empty_pw_code}'
login_code,login=req('POST','/api/login',{'username':username,'password':pw},bearer=False)
assert login_code==200 and isinstance(login,dict) and login.get('token'),'retained QA viewer could not login'
me_code,me=req('GET','/api/me',bearer=False)
# request helper accepts explicit bearer override below; do a separate request with the fresh Viewer token.
q=urllib.request.Request(base+'/api/me',headers={'Authorization':'Bearer '+login['token']},method='GET')
with urllib.request.urlopen(q,timeout=15) as r: me_code=r.status; me=json.loads(r.read())
viewer_role=me.get('role',me.get('Role'))
assert me_code==200 and viewer_role=='viewer',f'viewer role did not remain effective: HTTP {me_code}'
report={'case_ids':['API-CONTRACT'],'failures':['MAIN-USER-EMPTY-PASSWORD','MAIN-USER-INVALID-ROLE'],'status':'passed','hub_port':18080,'build_label':'dddee4f0-e2e-repair-20261004','hub_sha256':'e0b120e6e63fa5781cd4c5aef4a39f8094e799a4f5824bb969a816737d37c472','checks':{'empty_password_create_http':blank_create,'valid_fixture_create_http':create_code,'invalid_role_update_http':invalid_code,'empty_password_update_http':empty_pw_code,'viewer_role_after_rejections':role,'viewer_login_http':login_code,'viewer_me_http':me_code,'viewer_effective_role':viewer_role},'fixture':{'username':username,'user_id':uid,'retained_as':'valid Viewer (user DELETE unavailable; no invalid state persisted)'},'secrets_included':False}
p=Path(__file__).with_name('user-validation-live.json')
if p.exists(): raise SystemExit('refuse overwrite')
p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'passed','http':[blank_create,create_code,invalid_code,empty_pw_code,login_code,me_code],'effective_role':viewer_role,'retained_fixture':True}))
