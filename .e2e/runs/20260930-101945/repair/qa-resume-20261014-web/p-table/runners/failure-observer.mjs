// P-TABLE failure observer：对精确列表 GET 制造真实失败，采样 ErrorState/Retry。
// 参数：argv[2]=GET path，argv[3]=route。环境：PTABLE_SESSION / PTABLE_ORIGIN。
import { execFileSync } from 'node:child_process';
const SESSION = process.env.PTABLE_SESSION;
const ORIGIN = process.env.PTABLE_ORIGIN;
const allow = new Set(['/api/nodes', '/api/groups', '/api/scripts', '/api/tasks',
  '/api/schedules', '/api/applications', '/api/artifacts', '/api/executions',
  '/api/transfers', '/api/audit', '/api/users']);
const path = process.argv[2], route = process.argv[3];
if (!allow.has(path) || !route?.startsWith('/') || route.startsWith('//')) throw new Error('not_allowlisted');
const now = () => new Date().toISOString();
const cdpUrl = execFileSync('agent-browser', ['--session', SESSION, 'get', 'cdp-url'], { encoding: 'utf8' }).trim();
const endpoint = new URL(cdpUrl); if (!['127.0.0.1', 'localhost'].includes(endpoint.hostname)) throw new Error('cdp_not_loopback');
const pages = await (await fetch(`http://${endpoint.host}/json/list`)).json(); const pageTargets = pages.filter(x => x.type === 'page');
const owned = pageTargets.filter(x => x.url.startsWith(ORIGIN + '/')); const blank = pageTargets.filter(x => ['about:blank', 'data:,', 'chrome://newtab/', 'chrome://newtab'].includes(x.url)); const external = pageTargets.filter(x => !owned.includes(x) && !blank.includes(x));
if (owned.length !== 1 || external.length !== 0 || new URL(owned[0].url).pathname !== route) throw new Error('target_guard_failed');
const ws = new WebSocket(cdpUrl); let seq = 0, targetSession = null, fetchEnabled = false, cacheDisabled = false, attached = false, failCount = 0, errorState = false, settled = false, resolveDone;
const done = new Promise(r => resolveDone = r), pending = new Map(), failedNetworkIds = new Set(), loadingFailed = [];
function send(method, params = {}, sid = targetSession) { const id = ++seq; return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params, ...(sid ? { sessionId: sid } : {}) })) }) }
const expression = "(()=>{const m=document.querySelector('main'),t=m?.innerText||'',buttons=[...(m?.querySelectorAll('button')||[])];return {route:location.pathname,ready:document.readyState,alerts:m?.querySelectorAll('[role=\\\"alert\\\"]').length||0,retryButtons:buttons.filter(b=>/retry|重试/i.test(b.innerText||b.getAttribute('aria-label')||'')).length,skeletons:m?.querySelectorAll('[data-slot=\\\"skeleton\\\"]').length||0,rows:m?.querySelectorAll('tbody tr').length||0,loadingText:/loading|加载中/i.test(t)}})()";
async function sample() { try { const r = await send('Runtime.evaluate', { expression, returnByValue: true }); return r?.result?.value || null } catch { return null } }
async function disable() { if (fetchEnabled) { try { await send('Fetch.disable') } catch { } fetchEnabled = false } }
ws.onmessage = async ev => {
  let m; try { m = JSON.parse(ev.data) } catch { return }
  if (m.id && pending.has(m.id)) { const x = pending.get(m.id); pending.delete(m.id); if (m.error) x.reject(new Error('cdp_command_failed')); else x.resolve(m.result); return }
  if (m.sessionId !== targetSession || !m.method) return; const p = m.params || {};
  if (m.method === 'Network.requestWillBeSent') { try { const u = new URL(p.request.url); if (u.origin === ORIGIN && u.pathname === path && p.request.method === 'GET') failedNetworkIds.add(p.requestId) } catch { } }
  if (m.method === 'Fetch.requestPaused') { let match = false; try { const u = new URL(p.request.url); match = u.origin === ORIGIN && u.pathname === path && p.request.method === 'GET' } catch { }; if (match && failCount < 5) { failCount++; console.log(JSON.stringify({ phase: 'APP_GET_FAIL_REQUEST', path, method: 'GET', at: now(), attempt: failCount })); try { await send('Fetch.failRequest', { requestId: p.requestId, errorReason: 'Failed' }) } catch { } } else { try { await send('Fetch.continueRequest', { requestId: p.requestId }) } catch { } } }
  if (m.method === 'Network.loadingFailed' && failedNetworkIds.has(p.requestId)) { loadingFailed.push({ at: now(), errorClass: p.errorText === 'net::ERR_FAILED' ? 'net::ERR_FAILED' : p.errorText === 'net::ERR_ABORTED' ? 'net::ERR_ABORTED' : 'other', canceled: !!p.canceled }) }
};
try {
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('cdp_open_failed')) });
  const a = await send('Target.attachToTarget', { targetId: owned[0].id, flatten: true }, null); targetSession = a.sessionId; attached = true;
  await send('Network.enable'); await send('Network.setCacheDisabled', { cacheDisabled: true }); cacheDisabled = true;
  await send('Fetch.enable', { patterns: [{ urlPattern: ORIGIN + path + '*', requestStage: 'Request' }] }); fetchEnabled = true;
  console.log(JSON.stringify({ phase: 'READY', path, route, ownedPages: owned.length, externalPages: external.length, blankPages: blank.length, cdpPort: endpoint.port, at: now() }));
  const end = Date.now() + 20000; let last = null;
  while (Date.now() < end) { last = await sample(); if (last && last.alerts > 0 && last.retryButtons > 0) { errorState = true; break } await new Promise(r => setTimeout(r, 100)) }
  await disable(); settled = true;
  console.log(JSON.stringify({ phase: 'ERROR_STATE_SAMPLE', path, attempts: failCount, loadingFailedEvents: loadingFailed.length, errors: loadingFailed, errorState, retryVisible: !!last && last.retryButtons > 0, sample: last, at: now() }));
} finally {
  await disable();
  if (cacheDisabled) try { await send('Network.setCacheDisabled', { cacheDisabled: false }) } catch { }
  if (attached) try { await send('Target.detachFromTarget', { sessionId: targetSession }, null) } catch { }
  ws.close();
  console.log(JSON.stringify({ phase: 'FINISHED', path, fetchDisabled: !fetchEnabled, cacheRestored: cacheDisabled, errorState, attempts: failCount, loadingFailedEvents: loadingFailed.length, at: now() }))
}
