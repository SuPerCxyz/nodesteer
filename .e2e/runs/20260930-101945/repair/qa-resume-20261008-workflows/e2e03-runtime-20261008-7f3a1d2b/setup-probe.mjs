import { readFile, writeFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
const dir = new URL('.', import.meta.url).pathname
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const base = 'http://192.168.100.209:18080'
const nodeId = '37989297-3bc8-4c0c-b302-7c6efbdba630'
const nonce = randomUUID().slice(0, 8)
const probeName = `qa-resume-20261008-e2e03-probe-${nonce}`
const probeCommand = [
  'printf "uid=%s\\n" "$(id -u)"',
  'printf "username=%s\\n" "$(id -un)"',
  'printf "cwd=%s\\n" "$(pwd)"',
  'printf "python3=%s\\n" "$(command -v python3 || true)"',
  'printf "tmp_exists=%s\\n" "$(test -d /tmp && echo yes || echo no)"',
  'printf "tmp_writable=%s\\n" "$(test -w /tmp && echo yes || echo no)"',
  'for account in nobody daemon; do entry="$(getent passwd "$account" 2>/dev/null || true)"; if [ -n "$entry" ]; then printf "candidate_%s_uid=%s\\n" "$account" "$(printf "%s" "$entry" | cut -d: -f3)"; fi; done',
].join('\n')
const created = []
const record = { nonce, authority: base, node_id: nodeId, probe_task_ids: [], stages: [], status: 'RUNNING' }
let token
async function save(name, data) { await writeFile(`${dir}/${name}`, JSON.stringify(data, null, 2)) }
async function api(path, method = 'GET', body) {
  const r = await fetch(`${base}/api${path}`, { method, headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) })
  let data = null; try { data = await r.json() } catch {}
  record.stages.push({ method, path, status: r.status })
  return { status: r.status, body: data }
}
async function makeProbe(runUser = '') {
  const name = `${probeName}${runUser ? `-${runUser}` : ''}`
  const definition = { name, type: 'command', command: probeCommand, target: { type: 'node', node_ids: [nodeId] }, parameters: [], param_values: {}, timeout: 30, retry: 0, offline_policy: 'hub_online_required', enabled: true, ...(runUser ? { run_user: runUser } : {}) }
  const create = await api('/tasks', 'POST', definition)
  if (![200, 201].includes(create.status) || typeof create.body?.id !== 'string') throw new Error(`preflight Task create HTTP ${create.status}`)
  const id = create.body.id; created.push(id); record.probe_task_ids.push(id); await save('ledger.json', record)
  const readback = await api(`/tasks/${id}`)
  const exact = readback.status === 200 && readback.body?.target?.type === 'node' && readback.body.target.node_ids?.length === 1 && readback.body.target.node_ids[0] === nodeId && readback.body.retry === 0
  if (!exact) throw new Error('preflight Task definition/target mismatch')
  const run = await api(`/tasks/${id}/run`, 'POST', {})
  if (![200, 201].includes(run.status)) throw new Error(`preflight Task Run HTTP ${run.status}`)
  const executionIds = Array.isArray(run.body) ? run.body.map(x => x.id).filter(Boolean) : [run.body?.id].filter(Boolean)
  const end = Date.now() + 60_000
  let execution
  while (Date.now() < end) {
    const r = await api(`/executions?task_id=${id}&limit=10`)
    const rows = Array.isArray(r.body) ? r.body : r.body?.items || []
    execution = rows.find(x => executionIds.includes(x.id)) || rows[0]
    if (execution && ['SUCCESS', 'FAILED', 'TIMED_OUT', 'BLOCKED'].includes(execution.status)) break
    await new Promise(resolve => setTimeout(resolve, 1200))
  }
  if (!execution || !['SUCCESS', 'FAILED', 'TIMED_OUT', 'BLOCKED'].includes(execution.status)) throw new Error('preflight execution did not reach a terminal state')
  const parsed = Object.fromEntries(String(execution.stdout || '').split(/\r?\n/).map(line => { const i = line.indexOf('='); return i < 0 ? [line, ''] : [line.slice(0, i), line.slice(i + 1)] }))
  const summary = { task_id: id, run_user: runUser || null, execution_id: execution.id, status: execution.status, node_id: execution.node_id, exit_code: execution.exit_code, oracle: { uid: parsed.uid || null, username: parsed.username || null, cwd: parsed.cwd || null, python3_path: parsed.python3 || null, tmp_exists: parsed.tmp_exists || null, tmp_writable: parsed.tmp_writable || null, candidate_nobody_uid: parsed.candidate_nobody_uid || null, candidate_daemon_uid: parsed.candidate_daemon_uid || null } }
  record.preflight_runs = [...(record.preflight_runs || []), summary]
  await save('preflight.json', record)
  if (execution.status !== 'SUCCESS' || execution.node_id !== nodeId) throw new Error('preflight command execution did not succeed on native-3')
  return summary
}

try {
  const login = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: c.admin_username, password: c.admin_password }) })
  if (!login.ok) throw new Error(`repair API login HTTP ${login.status}`)
  const loginBody = await login.json(); token = loginBody.token || loginBody.Token
  if (typeof token !== 'string' || !token) throw new Error('process-only token unavailable')
  const nodes = await api('/nodes')
  const native3 = nodes.body?.find(x => x.id === nodeId)
  if (nodes.status !== 200 || native3?.status !== 'online' || native3?.sync_status !== 'synced') throw new Error('native-3 not online/synced')
  record.precondition = { node_id: nodeId, hostname: native3.hostname, status: native3.status, sync_status: native3.sync_status }
  await save('ledger.json', record)
  const defaultProbe = await makeProbe()
  if (!defaultProbe.oracle.python3_path || defaultProbe.oracle.tmp_exists !== 'yes' || defaultProbe.oracle.tmp_writable !== 'yes') throw new Error('default-user probe did not verify Python and /tmp')
  let selected
  if (Number(defaultProbe.oracle.uid) > 0) {
    selected = { username: defaultProbe.oracle.username, uid: Number(defaultProbe.oracle.uid), selection_reason: 'native-3 default Task identity is already non-root' }
  } else {
    const candidate = ['nobody', 'daemon'].find(name => Number(defaultProbe.oracle[`candidate_${name}_uid`]) > 0)
    if (!candidate) throw new Error('BLOCKED: no existing non-root nobody/daemon account found by read-only probe')
    let candidateProbe
    try { candidateProbe = await makeProbe(candidate) }
    catch (error) { throw new Error(`BLOCKED: existing non-root run_user probe could not execute: ${String(error?.message || error).slice(0, 180)}`) }
    if (candidateProbe.status !== 'SUCCESS' || Number(candidateProbe.oracle.uid) <= 0 || candidateProbe.oracle.username !== candidate || !candidateProbe.oracle.python3_path || candidateProbe.oracle.tmp_exists !== 'yes' || candidateProbe.oracle.tmp_writable !== 'yes') throw new Error('BLOCKED: existing non-root run_user probe failed or did not verify runtime')
    selected = { username: candidateProbe.oracle.username, uid: Number(candidateProbe.oracle.uid), selection_reason: 'read-only owned Task run_user probe verified existing non-root identity' }
  }
  record.selected_run_user = selected
  record.status = 'PREFLIGHT_READY'
} catch (error) {
  record.status = String(error?.message || error).startsWith('BLOCKED:') ? 'BLOCKED' : 'FAIL_OR_INCOMPLETE'
  record.error = String(error?.message || error).slice(0, 300)
} finally {
  for (const id of [...created].reverse()) {
    const del = await api(`/tasks/${id}`, 'DELETE')
    const get = await api(`/tasks/${id}`)
    record.cleanup = [...(record.cleanup || []), { task_id: id, delete_http: del.status, verify_get_http: get.status, clean: [200, 204, 404].includes(del.status) && get.status === 404 }]
    if (!record.cleanup.at(-1).clean) record.status = 'FAIL_CLEANUP'
  }
  delete c.admin_password; delete c.token; token = undefined
  await save('preflight.json', record)
  await save('ledger.json', record)
  process.stdout.write(JSON.stringify({ status: record.status, preflight_runs: record.preflight_runs?.map(x => ({ uid: x.oracle.uid, username: x.oracle.username, status: x.status, python3: Boolean(x.oracle.python3_path) })), selected_run_user: record.selected_run_user || null, probe_tasks_cleaned: record.cleanup?.every(x => x.clean) ?? false, error: record.error || null }) + '\n')
}
