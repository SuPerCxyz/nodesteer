import { execFileSync } from 'node:child_process';
const SESSION = 'qawebsecure20261010-b18-clean38181';
const ORIGIN = 'http://127.0.0.1:38181';
const PATH = process.argv[2];
if (!['/api/groups', '/api/scripts', '/api/tasks', '/api/schedules'].includes(PATH)) throw new Error('path_not_allowlisted');
const PAUSE_MS = 12000;
const MAX_MS = 30000;
const now = () => new Date().toISOString();
const cdpUrl = execFileSync('agent-browser', ['--session', SESSION, 'get', 'cdp-url'], { encoding: 'utf8' }).trim();
const endpoint = new URL(cdpUrl);
const httpOrigin = `http://${endpoint.host}`;
const targets = await (await fetch(`${httpOrigin}/json/list`)).json();
const pages = targets.filter(x => x.type === 'page');
const owned = pages.filter(x => x.url.startsWith(ORIGIN + '/'));
const blank = pages.filter(x => ['about:blank', 'data:,', 'chrome://newtab/', 'chrome://newtab'].includes(x.url));
const external = pages.filter(x => !owned.includes(x) && !blank.includes(x));
if (owned.length !== 1 || external.length !== 0) throw new Error('target_guard_failed');
const ws = new WebSocket(cdpUrl);
let seq = 0, targetSession, pausedRequestId, resumed = false, response200 = false, releaseTimer;
let resolveDone;
const done = new Promise(resolve => { resolveDone = resolve; });
const pending = new Map();
function send(method, params = {}, sessionId = targetSession) {
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
}
async function release(reason) {
  if (resumed || !pausedRequestId) return;
  resumed = true;
  if (releaseTimer) clearTimeout(releaseTimer);
  try { await send('Fetch.continueRequest', { requestId: pausedRequestId }); } catch {}
  console.log(JSON.stringify({ phase: 'CONTINUED', path: PATH, reason, at: now() }));
  if (response200) resolveDone();
}
ws.onmessage = async ev => {
  let msg;
  try { msg = JSON.parse(ev.data); } catch { return; }
  if (msg.id && pending.has(msg.id)) {
    const x = pending.get(msg.id); pending.delete(msg.id);
    if (msg.error) x.reject(new Error('cdp_command_failed'));
    else x.resolve(msg.result);
    return;
  }
  if (msg.sessionId !== targetSession || !msg.method) return;
  const p = msg.params || {};
  if (msg.method === 'Fetch.requestPaused') {
    let matches = false;
    try { const u = new URL(p.request.url); matches = u.origin === ORIGIN && u.pathname === PATH && p.request.method === 'GET'; } catch {}
    if (matches && !pausedRequestId) {
      pausedRequestId = p.requestId;
      console.log(JSON.stringify({ phase: 'APP_GET_PAUSED', method: 'GET', path: PATH, at: now() }));
      releaseTimer = setTimeout(() => { void release('bounded_timer'); }, PAUSE_MS);
    } else {
      try { await send('Fetch.continueRequest', { requestId: p.requestId }); } catch {}
    }
  }
  if (msg.method === 'Network.responseReceived') {
    try {
      const u = new URL(p.response.url);
      if (u.origin === ORIGIN && u.pathname === PATH && p.response.status === 200) {
        response200 = true;
        console.log(JSON.stringify({ phase: 'APP_GET_200', path: PATH, status: 200, at: now() }));
        if (resumed) resolveDone();
      }
    } catch {}
  }
};
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = () => reject(new Error('cdp_websocket_open_failed')); });
await send('Browser.getVersion', {}, null);
const attach = await send('Target.attachToTarget', { targetId: owned[0].id, flatten: true }, null);
targetSession = attach.sessionId;
await send('Network.enable');
await send('Network.setCacheDisabled', { cacheDisabled: true });
await send('Fetch.enable', { patterns: [{ urlPattern: ORIGIN + PATH, requestStage: 'Request' }] });
console.log(JSON.stringify({ phase: 'READY', owned_repair_pages: owned.length, external_pages: external.length, blank_pages: blank.length, cdp_port: endpoint.port, exact_path: PATH, auto_release_ms: PAUSE_MS }));
const timer = setTimeout(() => { void release('overall_timeout'); resolveDone(); }, MAX_MS);
await done;
if (releaseTimer) clearTimeout(releaseTimer);
try { await send('Fetch.disable'); } catch {}
try { await send('Network.setCacheDisabled', { cacheDisabled: false }); } catch {}
if (pausedRequestId && !resumed) await release('finally');
try { await send('Target.detachFromTarget', { sessionId: targetSession }, null); } catch {}
clearTimeout(timer);
console.log(JSON.stringify({ phase: 'FINISHED', path: PATH, paused: !!pausedRequestId, continued: resumed, appGet200: response200, finishedAt: now() }));
ws.close();
