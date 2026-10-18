// P-TABLE POST holder：精确暂停列表 POST，保持 pending 指定毫秒后放行，用于 G04-01 重复提交观测。
// 参数：argv[2]=POST path，argv[3]=route，argv[4]=holdMs。环境：PTABLE_SESSION / PTABLE_ORIGIN。
import { execFileSync } from 'node:child_process';
const SESSION = process.env.PTABLE_SESSION;
const ORIGIN = process.env.PTABLE_ORIGIN;
const path = process.argv[2], route = process.argv[3], holdMs = Number(process.argv[4] || 4000);
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
const ws = new WebSocket(cdpUrl); let seq = 0, targetSession = null, held = false, attached = false, fetchEnabled = false, cacheDisabled = false;
const pending = new Map(); let resolveDone; const done = new Promise(r => resolveDone = r);
function send(method, params = {}, sid = targetSession) { const id = ++seq; return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params, ...(sid ? { sessionId: sid } : {}) })) }) }
let postSeen = 0, continued = 0;
ws.onmessage = async ev => {
  let m; try { m = JSON.parse(ev.data) } catch { return }
  if (m.id && pending.has(m.id)) { const x = pending.get(m.id); pending.delete(m.id); if (m.error) x.reject(new Error('cdp_command_failed')); else x.resolve(m.result); return }
  if (m.sessionId !== targetSession || !m.method) return;
  const p = m.params || {};
  if (m.method === 'Fetch.requestPaused') {
    let match = false; try { const u = new URL(p.request.url); match = u.origin === ORIGIN && u.pathname === path && p.request.method === 'POST' } catch { }
    if (match && !held) {
      held = true; postSeen++;
      console.log(JSON.stringify({ phase: 'APP_POST_HELD', path, at: now(), holdMs }));
      setTimeout(async () => { try { await send('Fetch.continueRequest', { requestId: p.requestId }); continued++; console.log(JSON.stringify({ phase: 'APP_POST_RELEASED', at: now() })) } catch { } resolveDone() }, holdMs);
    } else if (match) {
      postSeen++;
      console.log(JSON.stringify({ phase: 'APP_POST_EXTRA_WHILE_PENDING', path, at: now(), count: postSeen }));
      try { await send('Fetch.continueRequest', { requestId: p.requestId }) } catch { }
    } else { try { await send('Fetch.continueRequest', { requestId: p.requestId }) } catch { } }
  }
};
try {
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('cdp_open_failed')) });
  const a = await send('Target.attachToTarget', { targetId: owned[0].id, flatten: true }, null); targetSession = a.sessionId; attached = true;
  await send('Network.enable'); await send('Network.setCacheDisabled', { cacheDisabled: true }); cacheDisabled = true;
  await send('Fetch.enable', { patterns: [{ urlPattern: ORIGIN + path + '*', requestStage: 'Request' }] }); fetchEnabled = true;
  console.log(JSON.stringify({ phase: 'READY', path, route, ownedPages: owned.length, externalPages: external.length, blankPages: blank.length, cdpPort: endpoint.port, at: now() }));
  const timer = setTimeout(() => resolveDone(), holdMs + 20000);
  await done; clearTimeout(timer);
} finally {
  if (fetchEnabled) try { await send('Fetch.disable') } catch { }
  if (cacheDisabled) try { await send('Network.setCacheDisabled', { cacheDisabled: false }) } catch { }
  if (attached) try { await send('Target.detachFromTarget', { sessionId: targetSession }, null) } catch { }
  ws.close();
  console.log(JSON.stringify({ phase: 'FINISHED', path, postSeen, continued, cacheRestored: cacheDisabled, at: now() }))
}
