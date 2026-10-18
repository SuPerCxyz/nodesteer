import { execFileSync } from 'node:child_process';
const SESSION = 'qawebsecure20261010-b18-clean38181';
const ORIGIN = 'http://127.0.0.1:38181';
const PATH = process.argv[2];
if (!['/api/groups', '/api/scripts', '/api/tasks', '/api/schedules'].includes(PATH)) throw new Error('path_not_allowlisted');
const now = () => new Date().toISOString();
const cdpUrl = execFileSync('agent-browser', ['--session', SESSION, 'get', 'cdp-url'], { encoding: 'utf8' }).trim();
const endpoint = new URL(cdpUrl);
const pages = await (await fetch(`http://${endpoint.host}/json/list`)).json();
const allPages = pages.filter(x => x.type === 'page');
const owned = allPages.filter(x => x.url.startsWith(ORIGIN + '/'));
const blank = allPages.filter(x => ['about:blank', 'data:,', 'chrome://newtab/', 'chrome://newtab'].includes(x.url));
const external = allPages.filter(x => !owned.includes(x) && !blank.includes(x));
if (owned.length !== 1 || external.length !== 0) throw new Error('target_guard_failed');
const ws = new WebSocket(cdpUrl);
let seq=0,targetSession,pausedRequestId,resumed=false,response200=false,resolveDone;
const done=new Promise(resolve=>{resolveDone=resolve});
const pending=new Map(),samples=[];
function send(method,params={},sessionId=targetSession){const id=++seq;return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}))})}
const expression="(()=>{const m=document.querySelector('main'),t=m?.innerText||'';return {route:location.pathname,navType:performance.getEntriesByType('navigation')[0]?.type||'none',ready:document.readyState,main:!!m,skeletons:m?.querySelectorAll('[data-slot=\\\"skeleton\\\"]').length||0,rows:m?.querySelectorAll('tbody tr').length||0,alerts:m?.querySelectorAll('[role=\\\"alert\\\"]').length||0,loading:/loading|加载中/i.test(t)}})()";
async function probe(){
  for(let i=0;i<20;i++){
    try{const x=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:false});const v=x?.result?.value;if(v){samples.push({at:now(),...v});if(v.skeletons>0)return v}}
    catch{}
    await new Promise(r=>setTimeout(r,100));
  }
  return samples.at(-1)||null;
}
async function release(reason){if(resumed||!pausedRequestId)return;resumed=true;try{await send('Fetch.continueRequest',{requestId:pausedRequestId})}catch{};console.log(JSON.stringify({phase:'CONTINUED',path:PATH,reason,at:now()}));if(response200)resolveDone()}
ws.onmessage=async ev=>{
 let m;try{m=JSON.parse(ev.data)}catch{return}
 if(m.id&&pending.has(m.id)){const x=pending.get(m.id);pending.delete(m.id);if(m.error)x.reject(new Error('cdp_command_failed'));else x.resolve(m.result);return}
 if(m.sessionId!==targetSession||!m.method)return;const p=m.params||{};
 if(m.method==='Fetch.requestPaused'){
  let match=false;try{const u=new URL(p.request.url);match=u.origin===ORIGIN&&u.pathname===PATH&&p.request.method==='GET'}catch{}
  if(match&&!pausedRequestId){pausedRequestId=p.requestId;console.log(JSON.stringify({phase:'APP_GET_PAUSED',method:'GET',path:PATH,at:now()}));const sample=await probe();console.log(JSON.stringify({phase:'DOM_SAMPLED',path:PATH,sampleCount:samples.length,skeletonCount:sample?.skeletons??null,rowCount:sample?.rows??null,alerts:sample?.alerts??null,route:sample?.route??null,navType:sample?.navType??null,ready:sample?.ready??null,firstSkeletonAt:samples.find(x=>x.skeletons>0)?.at||null}));await release('sample_complete')}
  else{try{await send('Fetch.continueRequest',{requestId:p.requestId})}catch{}}
 }
 if(m.method==='Network.responseReceived'){
  try{const u=new URL(p.response.url);if(u.origin===ORIGIN&&u.pathname===PATH&&p.response.status===200){response200=true;console.log(JSON.stringify({phase:'APP_GET_200',path:PATH,status:200,at:now()}));if(resumed)resolveDone()}}catch{}
 }
};
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=()=>reject(new Error('cdp_websocket_open_failed'))});
await send('Browser.getVersion',{},null);const a=await send('Target.attachToTarget',{targetId:owned[0].id,flatten:true},null);targetSession=a.sessionId;await send('Network.enable');await send('Network.setCacheDisabled',{cacheDisabled:true});await send('Fetch.enable',{patterns:[{urlPattern:ORIGIN+PATH,requestStage:'Request'}]});
console.log(JSON.stringify({phase:'READY',ownedPages:owned.length,externalPages:external.length,blankPages:blank.length,cdpPort:endpoint.port,path:PATH,cacheDisabledOnOwnedTarget:true}));
const timer=setTimeout(async()=>{await release('bounded_timeout');resolveDone()},30000);await done;clearTimeout(timer);try{await send('Fetch.disable')}catch{};try{await send('Network.setCacheDisabled',{cacheDisabled:false})}catch{};try{await send('Target.detachFromTarget',{sessionId:targetSession},null)}catch{};console.log(JSON.stringify({phase:'FINISHED',path:PATH,paused:!!pausedRequestId,continued:resumed,appGet200:response200,samples:samples.length,cacheRestored:true,finishedAt:now()}));ws.close();
