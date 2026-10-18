import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {readFileSync,writeFileSync} from 'node:fs';
const S='qawebsecure20261010-b18-clean38181',O='http://127.0.0.1:38181',B='.e2e/runs/20260930-101945/repair/qa-resume-20261010-web/batch23',mode=process.argv[2]||'baseline';
if(!['baseline','grant','deny-write','restore-grant','restore-baseline'].includes(mode))throw Error('invalid_mode');
const cdp=execFileSync('agent-browser',['--session',S,'get','cdp-url'],{encoding:'utf8'}).trim(),ep=new URL(cdp);
if(!['127.0.0.1','localhost'].includes(ep.hostname))throw Error('cdp_not_loopback');
const pages=await(await fetch(`http://${ep.host}/json/list`)).json(),pt=pages.filter(x=>x.type==='page'),own=pt.filter(x=>x.url.startsWith(O+'/')),blank=pt.filter(x=>['about:blank','data:,','chrome://newtab/','chrome://newtab'].includes(x.url)),ext=pt.filter(x=>!own.includes(x)&&!blank.includes(x));
if(own.length!==1||ext.length!==0||new URL(own[0].url).origin!==O)throw Error('owned_target_guard_failed');
const sha=x=>createHash('sha256').update(x).digest('hex'),ws=new WebSocket(cdp);let sid=null,seq=0,attached=false,pending=new Map();
function send(method,params={},session=null){const id=++seq;return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params,...(session?{sessionId:session}:{})}))})}
ws.onmessage=e=>{let m;try{m=JSON.parse(e.data)}catch{return}if(!m.id||!pending.has(m.id))return;const p=pending.get(m.id);pending.delete(m.id);m.error?p.reject(Object.assign(Error('cdp_command_failed'),{code:m.error.code})):p.resolve(m.result)};
async function getStates(){const ex=`(async()=>{const out={};for(const n of ['clipboard-read','clipboard-write']){try{out[n]=(await navigator.permissions.query({name:n})).state}catch{out[n]='unsupported'}}return JSON.stringify(out)})()`;let o=execFileSync('agent-browser',['--session',S,'eval',ex],{encoding:'utf8'}).trim();let v=JSON.parse(o);return typeof v==='string'?JSON.parse(v):v}
try{
 await new Promise((r,j)=>{ws.onopen=r;ws.onerror=()=>j(Error('cdp_open_failed'))});
 const targets=await send('Target.getTargets',{},null),info=targets.targetInfos.find(x=>x.targetId===own[0].id),ctxs=await send('Target.getBrowserContexts',{},null),nondefault=ctxs.browserContextIds||[];
 let contextParam={},contextScope='default_context_no_context_id';
 if(nondefault.length===1&&info?.browserContextId===nondefault[0]){contextParam={browserContextId:nondefault[0]};contextScope='single_owned_nondefault_context'}
 else if(nondefault.length===0){contextParam={};contextScope='default_context_omit_browserContextId'}
 else throw Error('ambiguous_browser_context_scope');
 const contextHash=info?.browserContextId?sha(info.browserContextId):null,before=await getStates();
 if(mode==='baseline'){
  const ev={captured_at_utc:new Date().toISOString(),session:S,origin:O,permissions:before,context_sha256:contextHash,context_scope:contextScope,nondefault_context_count:nondefault.length,owned_page_count:own.length,external_page_count:ext.length,raw_context_id_saved:false};
  writeFileSync(B+'/evidence/clipboard-permission-baseline.json',JSON.stringify(ev,null,2)+'\n');
  console.log(JSON.stringify({mode,origin:O,contextSha256:contextHash,contextScope,nondefaultContextCount:nondefault.length,permissions:before,baselineSaved:true}));
 }else{
  let desired={};
  if(mode==='grant'||mode==='restore-grant')desired={'clipboard-read':'granted','clipboard-write':'granted'};
  else if(mode==='deny-write')desired={'clipboard-write':'denied'};
  else{const saved=JSON.parse(readFileSync(B+'/evidence/clipboard-permission-baseline.json','utf8')).permissions;desired={'clipboard-read':saved['clipboard-read']==='unsupported'?'prompt':saved['clipboard-read'],'clipboard-write':saved['clipboard-write']==='unsupported'?'prompt':saved['clipboard-write']}}
  const a=await send('Target.attachToTarget',{targetId:own[0].id,flatten:true},null);sid=a.sessionId;attached=true;const changes=[];
  for(const [name,setting] of Object.entries(desired)){await send('Browser.setPermission',{origin:O,...contextParam,permission:{name,allowWithoutSanitization:false},setting},null);changes.push({permission:name,setting,ok:true})}
  const after=await getStates();console.log(JSON.stringify({mode,origin:O,contextSha256:contextHash,contextScope,ownedPages:own.length,externalPages:ext.length,blankPages:blank.length,before,changes,after,permissionsMatchRequested:Object.entries(desired).every(([n,s])=>after[n]===s)}));
 }
}finally{if(attached)try{await send('Target.detachFromTarget',{sessionId:sid},null)}catch{};ws.close()}
