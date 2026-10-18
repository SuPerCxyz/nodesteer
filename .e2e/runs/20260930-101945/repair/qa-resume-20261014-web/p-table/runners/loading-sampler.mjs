// P-TABLE loading sampler：暂停精确列表 GET，采样同目标上的真实 Loading 反馈。
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
const endpoint = new URL(cdpUrl);
if (!['127.0.0.1', 'localhost'].includes(endpoint.hostname)) throw new Error('cdp_not_loopback');
const pages = await (await fetch(`http://${endpoint.host}/json/list`)).json();
const pageTargets = pages.filter(x => x.type === 'page');
const owned = pageTargets.filter(x => x.url.startsWith(ORIGIN + '/'));
const blank = pageTargets.filter(x => ['about:blank', 'data:,', 'chrome://newtab/', 'chrome://newtab'].includes(x.url));
const external = pageTargets.filter(x => !owned.includes(x) && !blank.includes(x));
if (owned.length !== 1 || external.length !== 0 || new URL(owned[0].url).pathname !== route) throw new Error('target_guard_failed');
const ws = new WebSocket(cdpUrl); let seq = 0, targetSession = null, paused = null, continued = false, got200 = false, resolveDone;
const done = new Promise(r => resolveDone = r), pending = new Map(), samples = [];
function send(method, params = {}, sid = targetSession) { const id = ++seq; return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params, ...(sid ? { sessionId: sid } : {}) })) }) }
const expression = "(()=>{const m=document.querySelector('main'),t=m?.innerText||'';return {route:location.pathname,ready:document.readyState,navType:performance.getEntriesByType('navigation')[0]?.type||'none',main:!!m,skeletons:m?.querySelectorAll('[data-slot=\\\"skeleton\\\"]').length||0,progressbars:m?.querySelectorAll('[role=\\\"progressbar\\\"]').length||0,spinners:m?.querySelectorAll('svg.animate-spin').length||0,ariaBusy:m?.querySelectorAll('[aria-busy=\\\"true\\\"]').length||0,rows:m?.querySelectorAll('tbody tr').length||0,alerts:m?.querySelectorAll('[role=\\\"alert\\\"]').length||0,loadingText:/loading|加载中/i.test(t)}})()";
async function probe() { const end = Date.now() + 1800; while (Date.now() < end) { try { const x = await send('Runtime.evaluate', { expression, returnByValue: true }); if (x?.result?.value) samples.push({ at: now(), ...x.result.value }) } catch { }; await new Promise(r => setTimeout(r, 100)) } return samples.at(-1) || null }
async function release() { if (!paused || continued) return; continued = true; await send('Fetch.continueRequest', { requestId: paused }); }
ws.onmessage = async ev => {
  let m; try { m = JSON.parse(ev.data) } catch { return }
  if (m.id && pending.has(m.id)) { const x = pending.get(m.id); pending.delete(m.id); if (m.error) x.reject(new Error('cdp_command_failed')); else x.resolve(m.result); return }
  if (m.sessionId !== targetSession || !m.method) return; const p = m.params || {};
  if (m.method === 'Fetch.requestPaused') { let match = false; try { const u = new URL(p.request.url); match = u.origin === ORIGIN && u.pathname === path && p.request.method === 'GET' } catch { }; if (match && !paused) { paused = p.requestId; console.log(JSON.stringify({ phase: 'APP_GET_PAUSED', method: 'GET', path, at: now() })); const result = await probe(); console.log(JSON.stringify({ phase: 'LOADING_SAMPLED', path, samples: samples.length, loadingSamples: samples.filter(x => x.skeletons || x.progressbars || x.spinners || x.ariaBusy || x.loadingText).length, sample: result ? { route: result.route, ready: result.ready, navType: result.navType, skeletons: result.skeletons, progressbars: result.progressbars, spinners: result.spinners, ariaBusy: result.ariaBusy, rows: result.rows, alerts: result.alerts, loadingText: result.loadingText } : null })); await release() } else { try { await send('Fetch.continueRequest', { requestId: p.requestId }) } catch { } } }
  if (m.method === 'Network.responseReceived') { try { const u = new URL(p.response.url); if (u.origin === ORIGIN && u.pathname === path && p.response.status === 200) { got200 = true; console.log(JSON.stringify({ phase: 'APP_GET_200', path, status: 200, at: now() })); if (continued) resolveDone() } } catch { } }
};
let cacheChanged = false, fetchEnabled = false, attached = false;
try {
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('cdp_open_failed')) });
  const a = await send('Target.attachToTarget', { targetId: owned[0].id, flatten: true }, null); targetSession = a.sessionId; attached = true;
  await send('Network.enable'); await send('Network.setCacheDisabled', { cacheDisabled: true }); cacheChanged = true;
  await send('Fetch.enable', { patterns: [{ urlPattern: ORIGIN + path + '*', requestStage: 'Request' }] }); fetchEnabled = true;
  console.log(JSON.stringify({ phase: 'READY', path, route, ownedPages: owned.length, externalPages: external.length, blankPages: blank.length, cdpPort: endpoint.port, cacheDisabledOnOwnedTarget: true, at: now() }));
  const timer = setTimeout(async () => { if (paused) await release(); resolveDone() }, 18000); await done; clearTimeout(timer)
} finally {
  if (fetchEnabled) try { await send('Fetch.disable') } catch { }
  if (cacheChanged) try { await send('Network.setCacheDisabled', { cacheDisabled: false }) } catch { }
  if (attached) try { await send('Target.detachFromTarget', { sessionId: targetSession }, null) } catch { }
  ws.close();
  console.log(JSON.stringify({ phase: 'FINISHED', path, paused: !!paused, continued, appGet200: got200, loadingSamples: samples.filter(x => x.skeletons || x.progressbars || x.spinners || x.ariaBusy || x.loadingText).length, totalSamples: samples.length, cacheRestored: cacheChanged, at: now() }))
}
