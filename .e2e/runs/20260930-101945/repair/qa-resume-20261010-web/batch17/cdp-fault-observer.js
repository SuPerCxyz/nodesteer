#!/usr/bin/env node
// Narrow CDP observer/failpoint for the currently active owned page. It logs only
// the allowlisted path, method, status, and network errorText; never URL query,
// headers, bodies, page text, or target/title.
import { execFileSync } from 'node:child_process'
import readline from 'node:readline'
const ORIGIN = 'http://127.0.0.1:38181'
const wantedPath = process.argv[2]
if (!/^\/api\/[a-z-]+$/.test(wantedPath || '')) process.exit(2)
const cdpUrl = execFileSync('agent-browser', ['get', 'cdp-url'], {
  encoding: 'utf8',
  env: process.env,
  stdio: ['ignore', 'pipe', 'ignore'],
}).trim()
const cdp = new URL(cdpUrl)
const listURL = `http://${cdp.hostname}:${cdp.port}/json/list`
const targets = await fetch(listURL).then((r) => r.json())
const matching = targets.filter((t) => {
  if (t.type !== 'page') return false
  try {
    const u = new URL(t.url)
    return u.origin === ORIGIN && u.pathname === wantedPath.replace(/^\/api/, '')
  } catch {
    return false
  }
})
if (matching.length !== 1) {
  process.stdout.write(JSON.stringify({ ready: false, owned_page_count: matching.length }) + '\n')
  process.exit(3)
}
const ws = new WebSocket(matching[0].webSocketDebuggerUrl)
const pending = new Map()
const pathByRequest = new Map()
const failed = []
const responses = []
let id = 0
let disabled = false
let responseWatch = false
function send(method, params = {}) {
  const requestId = ++id
  return new Promise((resolve, reject) => {
    pending.set(requestId, { resolve, reject })
    ws.send(JSON.stringify({ id: requestId, method, params }))
  })
}
function cleanResponse() {
  return {
    ready: true,
    path: wantedPath,
    paused_get_count: failed.length,
    network_loading_failed: failed.map((x) => ({ path: x.path, method: x.method, error_text: x.errorText || null })),
    recovered_get_statuses: responses.map((x) => x.status),
  }
}
ws.addEventListener('message', async (ev) => {
  let msg
  try { msg = JSON.parse(ev.data) } catch { return }
  if (msg.id && pending.has(msg.id)) {
    const p = pending.get(msg.id)
    pending.delete(msg.id)
    if (msg.error) p.reject(new Error('cdp_command_failed'))
    else p.resolve(msg.result)
    return
  }
  const e = msg.params || {}
  if (msg.method === 'Network.requestWillBeSent') {
    try {
      const u = new URL(e.request.url)
      if (u.origin === ORIGIN && u.pathname === wantedPath) pathByRequest.set(e.requestId, { path: u.pathname, method: e.request.method })
    } catch {}
  } else if (msg.method === 'Fetch.requestPaused') {
    try {
      const u = new URL(e.request.url)
      if (e.responseStatusCode !== undefined) {
        if (u.origin === ORIGIN && u.pathname === wantedPath) responses.push({ status: e.responseStatusCode, source: 'FetchResponsePaused' })
        await send('Fetch.continueResponse', { requestId: e.requestId })
      } else if (u.origin === ORIGIN && u.pathname === wantedPath && e.request.method === 'GET' && !disabled && !responseWatch) {
        failed.push({ path: u.pathname, method: e.request.method, errorText: null })
        await send('Fetch.failRequest', { requestId: e.requestId, errorReason: 'Failed' })
      } else {
        await send('Fetch.continueRequest', { requestId: e.requestId })
      }
    } catch {
      try {
        if (e.responseStatusCode !== undefined) await send('Fetch.continueResponse', { requestId: e.requestId })
        else await send('Fetch.continueRequest', { requestId: e.requestId })
      } catch {}
    }
  } else if (msg.method === 'Network.loadingFailed') {
    const req = pathByRequest.get(e.requestId)
    if (req && req.path === wantedPath) {
      const target = [...failed].reverse().find((x) => !x.errorText)
      if (target) target.errorText = e.errorText || 'loading_failed'
    }
  } else if (msg.method === 'Network.responseReceived') {
    try {
      const u = new URL(e.response.url)
      if (u.origin === ORIGIN && u.pathname === wantedPath) responses.push({ status: e.response.status, source: 'responseReceived' })
    } catch {}
  } else if (msg.method === 'Network.responseReceivedExtraInfo') {
    if (pathByRequest.get(e.requestId)?.path === wantedPath) responses.push({ status: e.statusCode, source: 'responseReceivedExtraInfo' })
  }
})
await new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve, { once: true })
  ws.addEventListener('error', () => reject(new Error('cdp_websocket_error')), { once: true })
})
await send('Network.enable')
await send('Network.setCacheDisabled', { cacheDisabled: true })
await send('Fetch.enable', { patterns: [{ urlPattern: `${ORIGIN}${wantedPath}*`, requestStage: 'Request' }] })
process.stdout.write(JSON.stringify({ ready: true, path: wantedPath, owned_page_count: 1, page_cache_disabled: true }) + '\n')
const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity })
for await (const line of rl) {
  if (line.trim() === 'disable' && !disabled) {
    disabled = true
    await send('Fetch.disable')
    process.stdout.write(JSON.stringify({ event: 'interception_disabled', ...cleanResponse() }) + '\n')
  } else if (line.trim() === 'watch-response' && disabled) {
    responseWatch = true
    disabled = false
    await send('Fetch.enable', { patterns: [{ urlPattern: `${ORIGIN}${wantedPath}*`, requestStage: 'Response' }] })
    process.stdout.write(JSON.stringify({ event: 'response_watch_ready', path: wantedPath }) + '\n')
  } else if (line.trim() === 'stop') {
    if (!disabled) { disabled = true; await send('Fetch.disable') }
    await send('Network.setCacheDisabled', { cacheDisabled: false })
    process.stdout.write(JSON.stringify({ event: 'final', page_cache_restored: true, ...cleanResponse() }) + '\n')
    ws.close()
    process.exit(0)
  }
}
