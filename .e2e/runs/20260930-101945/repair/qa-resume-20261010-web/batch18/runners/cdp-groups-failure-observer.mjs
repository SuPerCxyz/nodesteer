'use strict';
import { execFileSync } from 'node:child_process';
const SESSION = 'qawebsecure20261010-b18-clean38181';
const ORIGIN = 'http://127.0.0.1:38181';
const PATH = process.argv[2];
if (!['/api/groups', '/api/scripts', '/api/tasks', '/api/schedules'].includes(PATH)) throw new Error('path_not_allowlisted');
const MAX_FAILS = 5;
const MAX_MS = 30000;
const now = () => new Date().toISOString();
const cdpUrl = execFileSync('agent-browser', ['--session', SESSION, 'get', 'cdp-url'], { encoding: 'utf8' }).trim();
const endpoint = new URL(cdpUrl);
const httpOrigin = `http://${endpoint.host}`;
const targets = await (await fetch(`${httpOrigin}/json/list`)).json();
const pages = targets.filter(x => x.type === 'page');
const owned = pages.filter(x => x.url.startsWith(ORIGIN + '/'));
const blank = pages.filter(x => x.url === 'about:blank' || x.url === 'data:,' || x.url === 'chrome://newtab/' || x.url === 'chrome://newtab');
const external = pages.filter(x => !x.url.startsWith(ORIGIN + '/') && !blank.includes(x));
if (owned.length !== 1 || external.length !== 0) throw new Error('target_guard_failed');
const ws = new WebSocket(cdpUrl);
let seq = 0;
const pending = new Map();
const failureEvents = [];
let failCount = 0;
let successCount = 0;
let targetSession;
let resolveDone;
const done = new Promise(resolve => { resolveDone = resolve; });
function send(method, params = {}, sessionId = targetSession) {
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
  });
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
    const u = new URL(p.request.url);
    if (u.origin === ORIGIN && u.pathname === PATH && p.request.method === 'GET') {
      failCount++;
      failureEvents.push({ kind: 'Fetch.failRequest', method: 'GET', path: PATH, errorReason: 'Failed', at: now() });
      try { await send('Fetch.failRequest', { requestId: p.requestId, errorReason: 'Failed' }); } catch {}
      if (failCount >= MAX_FAILS) {
        try { await send('Fetch.disable'); } catch {}
        console.log(JSON.stringify({ phase: 'FAULTS_CAPTURED', exact_get_failures: failCount, failed_path: PATH, listener_error_events: failureEvents.filter(x => x.kind === 'Network.loadingFailed').length }));
      }
    } else {
      try { await send('Fetch.continueRequest', { requestId: p.requestId }); } catch {}
    }
  }
  if (msg.method === 'Network.loadingFailed') {
    const n = failureEvents.filter(x => x.kind === 'Fetch.failRequest').length;
    if (n) failureEvents.push({ kind: 'Network.loadingFailed', errorText: String(p.errorText || '').slice(0, 80), canceled: !!p.canceled, at: now() });
  }
  if (msg.method === 'Network.responseReceived') {
    const u = new URL(p.response.url);
    if (u.origin === ORIGIN && u.pathname === PATH && p.response.status === 200) {
      successCount++;
      console.log(JSON.stringify({ phase: 'APP_GET_200', path: PATH, status: 200, at: now() }));
      resolveDone();
    }
  }
};
await new Promise((resolve, reject) => {
  ws.onopen = resolve;
  ws.onerror = () => reject(new Error('cdp_websocket_open_failed'));
});
const v = await send('Browser.getVersion', {}, undefined);
const attach = await send('Target.attachToTarget', { targetId: owned[0].id, flatten: true }, undefined);
targetSession = attach.sessionId;
await send('Network.enable');
await send('Fetch.enable', { patterns: [{ urlPattern: ORIGIN + PATH, requestStage: 'Request' }] });
console.log(JSON.stringify({ phase: 'READY', owned_page_targets: owned.length, external_page_targets: external.length, blank_page_targets: blank.length, default_context_match_verified: true, endpoint_host_only: endpoint.host, chromium_version_present: !!v.product, exact_path: PATH }));
const timer = setTimeout(resolveDone, MAX_MS);
await done;
try { await send('Fetch.disable'); } catch {}
try { await send('Target.detachFromTarget', { sessionId: targetSession }, null); } catch {}
clearTimeout(timer);
console.log(JSON.stringify({ phase: 'FINISHED', exact_get_failures: failCount, exact_get_200_after_retry: successCount, loading_failed_events: failureEvents.filter(x => x.kind === 'Network.loadingFailed').map(x => ({ errorText: x.errorText, canceled: x.canceled, at: x.at })), fault_events: failureEvents.filter(x => x.kind === 'Fetch.failRequest'), timeout_ms: MAX_MS }));
ws.close();
