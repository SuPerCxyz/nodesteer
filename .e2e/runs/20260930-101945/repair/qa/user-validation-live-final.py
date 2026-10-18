#!/usr/bin/env python3
"""Verify user validation/authentication against final Hub; use only fresh QA accounts."""
import json,secrets,urllib.error,urllib.request
from pathlib import Path
RUN='20260930-101945';priv=Path('/tmp/nodesteer-e2e-'+RUN+'-repair');conn=json.loads((priv/'repair-connection.json').read_text());base=conn['hub_url'].rstrip('/');token=conn['token'];out=Path(__file__).with_name('user-validation-live-final.json')
if out.exists():raise SystemExit('refuse overwrite final user evidence')
def req(method,path,body=None,bearer=None):
 data=None if body is None else json.dumps(body).encode();headers={'Content-Type':'application/json'} if data is not None else {}
 active=token if bearer is None else bearer
 if active:headers['Authorization']='Bearer '+active
 try:
  with urllib.request.urlopen(urllib.request.Request(base+path,data=data,headers=headers,method=method),timeout=15) as r:code,raw=r.status,r.read()
 except urllib.error.HTTPError as e:code,raw=e.code,e.read()
 try:value=json.loads(raw) if raw else None
 except (ValueError,UnicodeDecodeError):value=None
 return code,value
# Empty credentials are rejected at the auth boundary before looking up or creating any identity.
login_empty_both,_=req('POST','/api/login',{'username':'','password':''},bearer='')
login_empty_user,_=req('POST','/api/login',{'username':'','password':'not-a-credential'},bearer='')
login_empty_password,_=req('POST','/api/login',{'username':conn['admin_username'],'password':''},bearer='')
assert (login_empty_both,login_empty_user,login_empty_password)==(401,401,401)
empty_create_name='qa-repair-'+RUN+'-empty-'+secrets.token_hex(4)
empty_create,_=req('POST','/api/users',{'username':empty_create_name,'password':'','role':'viewer'})
invalid_post_name='qa-repair-'+RUN+'-invalid-role-'+secrets.token_hex(4)
invalid_post,_=req('POST','/api/users',{'username':invalid_post_name,'password':'QaValid-'+secrets.token_urlsafe(18),'role':'qa_invalid_role'})
assert empty_create in (400,422) and invalid_post in (400,422),(empty_create,invalid_post)
# Reuse only the previously created QA Viewer; reset its password to a fresh nonempty dummy credential.
prior=json.loads(Path(__file__).with_name('user-validation-live.json').read_text());viewer_id=prior['fixture']['user_id'];viewer_name=prior['fixture']['username']
viewer_pw='QaRepair-'+secrets.token_urlsafe(24);attempted_pw='QaRepair-'+secrets.token_urlsafe(24)
setpw,_=req('PUT','/api/users/'+viewer_id,{'password':viewer_pw});assert setpw in (200,204),f'QA Viewer password reset HTTP {setpw}'
viewer_login,viewer_s=req('POST','/api/login',{'username':viewer_name,'password':viewer_pw},bearer='');assert viewer_login==200 and viewer_s.get('token')
viewer_token=viewer_s['token'];me_code,viewer_me=req('GET','/api/me',bearer=viewer_token)
assert me_code==200 and viewer_me.get('role',viewer_me.get('Role'))=='viewer'
invalid_put,_=req('PUT','/api/users/'+viewer_id,{'role':'qa_invalid_role'})
invalid_pair,_=req('PUT','/api/users/'+viewer_id,{'role':'qa_invalid_role','password':attempted_pw})
empty_update,_=req('PUT','/api/users/'+viewer_id,{'password':''})
assert invalid_put in (400,422) and invalid_pair in (400,422) and empty_update in (400,422),(invalid_put,invalid_pair,empty_update)
users_code,users=req('GET','/api/users');assert users_code==200 and isinstance(users,list)
row=next((x for x in users if (x.get('id') or x.get('ID'))==viewer_id),None)
assert row and row.get('role',row.get('Role'))=='viewer','invalid update partially changed role'
oldpw_login,oldpw=req('POST','/api/login',{'username':viewer_name,'password':viewer_pw},bearer='')
newpw_login,_=req('POST','/api/login',{'username':viewer_name,'password':attempted_pw},bearer='')
assert oldpw_login==200 and newpw_login==401,'invalid role+password update was not atomic'
viewer_denied,_=req('GET','/api/users',bearer=oldpw['token']);viewer_still,viewer_after=req('GET','/api/me',bearer=oldpw['token'])
assert viewer_denied==403 and viewer_still==200 and viewer_after.get('role',viewer_after.get('Role'))=='viewer'
# A fresh valid Operator login complements Admin and the existing QA Viewer role path.
operator_name='qa-repair-'+RUN+'-operator-'+secrets.token_hex(4);operator_pw='QaRepair-'+secrets.token_urlsafe(24)
operator_create,operator=req('POST','/api/users',{'username':operator_name,'password':operator_pw,'role':'operator'})
assert operator_create in (200,201) and operator.get('id'),f'Operator fixture create HTTP {operator_create}'
operator_login,op_session=req('POST','/api/login',{'username':operator_name,'password':operator_pw},bearer='')
assert operator_login==200 and op_session.get('token')
op_me_code,op_me=req('GET','/api/me',bearer=op_session['token']);op_forbidden,_=req('GET','/api/users',bearer=op_session['token'])
assert op_me_code==200 and op_me.get('role',op_me.get('Role'))=='operator' and op_forbidden==403
admin_me_code,admin_me=req('GET','/api/me',bearer=token);assert admin_me_code==200 and admin_me.get('role',admin_me.get('Role'))=='administrator'
report={'case_ids':['API-CONTRACT'],'failure_ids':['MAIN-USER-EMPTY-PASSWORD','MAIN-USER-INVALID-ROLE'],'status':'passed','layer':'live-isolated-final-static-hub','hub_sha256':'164ea4aca898feb1e118ef9b45f02d76482dd4745cef1d9598cc78c7569132d6','agent_sha256':'d55dc5cd9b1a697a76f0bf6dccb3347b7715ef760173d31d1d1e57db96373add','build_label':'dddee4f0-e2e-repair-20261004','checks':{'empty_login_http':[login_empty_both,login_empty_user,login_empty_password],'empty_password_create_http':empty_create,'invalid_role_create_http':invalid_post,'invalid_role_update_http':invalid_put,'invalid_role_plus_password_http':invalid_pair,'empty_password_update_http':empty_update,'invalid_update_role_after':row.get('role',row.get('Role')),'old_password_after_rejected_pair_http':oldpw_login,'attempted_new_password_after_rejected_pair_http':newpw_login,'viewer_forbidden_users_http':viewer_denied,'viewer_me_after_403_http':viewer_still,'operator_create_http':operator_create,'operator_login_me_forbidden_http':[operator_login,op_me_code,op_forbidden],'admin_me_http':admin_me_code,'all_valid_role_names':['administrator','operator','viewer']},'fixtures':{'qa_viewer_id':viewer_id,'qa_operator_id':operator['id'],'qa_operator_username':operator_name,'accounts_retained_as_valid_roles':'User DELETE endpoint unavailable; only fresh QA accounts were created/reset'},'legacy_empty_password_account_test':'not attempted against live Hub; no legacy user or primary account modified. Auth guard is exercised by empty username/password API requests, legacy-row compatibility belongs to store/auth regression.','secrets_included':False}
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':'passed','empty_login':[login_empty_both,login_empty_user,login_empty_password],'empty_create':empty_create,'invalid_post_put_pair':[invalid_post,invalid_put,invalid_pair],'empty_update':empty_update,'viewer_atomic_old_new_login':[oldpw_login,newpw_login],'roles':['administrator','operator','viewer']}))
