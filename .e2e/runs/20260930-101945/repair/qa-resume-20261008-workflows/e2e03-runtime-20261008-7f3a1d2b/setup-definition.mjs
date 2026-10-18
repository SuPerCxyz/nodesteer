import { createHash, randomUUID } from 'node:crypto'
import { readFile, writeFile } from 'node:fs/promises'
const dir = new URL('.', import.meta.url).pathname
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const preflight = JSON.parse(await readFile(`${dir}/preflight.json`, 'utf8'))
if (preflight.status !== 'PREFLIGHT_READY' || !preflight.selected_run_user) throw new Error('Preflight is not ready')
const base = 'http://192.168.100.209:18080'
const nodeId = '37989297-3bc8-4c0c-b302-7c6efbdba630'
const username = preflight.selected_run_user.username
const uid = Number(preflight.selected_run_user.uid)
if (!username || uid <= 0 || username === 'root') throw new Error('Selected run_user is not a confirmed non-root account')
const suffix = randomUUID().slice(0, 8)
const prefix = `qa-resume-20261008-e2e03-${suffix}`
const parameterMarker = `${prefix}-parameter-marker`
const environmentMarker = `${prefix}-environment-marker`
const source = [
  '#!/bin/sh',
  'set -eu',
  'printf "shell_cwd=%s\\n" "$(pwd)"',
  'printf "uid=%s\\n" "$(id -u)"',
  'printf "username=%s\\n" "$(id -un)"',
  'printf "parameter=%s\\n" "${NODESTEER_PARAM_qa_input-}"',
  'printf "environment=%s\\n" "${QA_E2E_MARKER-}"',
  `python3 -c 'import os,pwd; print("python_cwd="+os.getcwd()); print("python_uid="+str(os.getuid())); print("python_username="+pwd.getpwuid(os.getuid()).pw_name)'`,
].join('\n') + '\n'
const sourceSha256 = createHash('sha256').update(source, 'utf8').digest('hex')
const expected = { working_dir: '/tmp', uid, username, parameter: parameterMarker, environment: environmentMarker, python3_path: preflight.preflight_runs.find(x => x.run_user === username)?.oracle.python3_path || preflight.preflight_runs.find(x => x.oracle.username === username)?.oracle.python3_path }
if (!expected.python3_path) throw new Error('Python3 path was not confirmed by owned read-only probe')
await writeFile(`${dir}/source/script.sh`, source)
await writeFile(`${dir}/source/metadata.json`, JSON.stringify({ interpreter: 'shell', source_file: 'source/script.sh', source_sha256: sourceSha256, byte_length: Buffer.byteLength(source), api_sha256_matches_expected: 'PENDING' }, null, 2))
await writeFile(`${dir}/definition/expected-oracle.json`, JSON.stringify({ target_node_id: nodeId, selected_run_user: { username, uid }, expected, parameter_marker: parameterMarker, environment_marker: environmentMarker }, null, 2))

let token, scriptId, taskId
const calls = []
const ledger = { status: 'SETTING_UP', script_id: null, task_id: null, source_sha256: sourceSha256, preflight_status: preflight.status, cleanup: [] }
async function api(path, method = 'GET', data) {
  const r = await fetch(`${base}/api${path}`, { method, headers: { Authorization: `Bearer ${token}`, ...(data === undefined ? {} : { 'Content-Type': 'application/json' }) }, ...(data === undefined ? {} : { body: JSON.stringify(data) }) })
  let body = null; try { body = await r.json() } catch {}
  calls.push({ method, path, status: r.status })
  return { status: r.status, body }
}
async function persist() {
  ledger.calls = calls
  await writeFile(`${dir}/ledger.json`, JSON.stringify(ledger, null, 2))
}
try {
  const login = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: c.admin_username, password: c.admin_password }) })
  if (!login.ok) throw new Error(`repair API login HTTP ${login.status}`)
  const loginBody = await login.json(); token = loginBody.token || loginBody.Token
  if (typeof token !== 'string' || !token) throw new Error('process-only token unavailable')
  const nodes = await api('/nodes')
  const native3 = nodes.body?.find(x => x.id === nodeId)
  if (nodes.status !== 200 || native3?.status !== 'online' || native3?.sync_status !== 'synced') throw new Error('native-3 no longer online/synced')

  const scriptDefinition = {
    name: `${prefix}-script`, description: 'isolated E2E03 runtime field consumption probe', interpreter: 'shell', content: source,
    parameters: [{ name: 'qa_input', type: 'string', required: true, default: 'script-default' }],
    environment: { QA_E2E_MARKER: environmentMarker }, working_dir: '/tmp', run_user: username, timeout: 60, enabled: true,
  }
  const scriptCreate = await api('/scripts', 'POST', scriptDefinition)
  if (![200, 201].includes(scriptCreate.status) || typeof scriptCreate.body?.id !== 'string') throw new Error(`Script setup HTTP ${scriptCreate.status}`)
  scriptId = scriptCreate.body.id; ledger.script_id = scriptId; await persist()
  const scriptRead = await api(`/scripts/${scriptId}`)
  if (scriptRead.status !== 200) throw new Error(`Script readback HTTP ${scriptRead.status}`)
  const savedScript = scriptRead.body
  const scriptMatch = savedScript.content === source && savedScript.sha256 === sourceSha256 && savedScript.working_dir === '/tmp' && savedScript.run_user === username && savedScript.environment?.QA_E2E_MARKER === environmentMarker && savedScript.parameters?.some(p => p.name === 'qa_input' && p.required && p.default === 'script-default')
  if (!scriptMatch) throw new Error('Script API definition differs from saved source/expected fields')
  await writeFile(`${dir}/definition/script-api-readback.json`, JSON.stringify(savedScript, null, 2))
  await writeFile(`${dir}/source/metadata.json`, JSON.stringify({ interpreter: savedScript.interpreter, source_file: 'source/script.sh', source_sha256: sourceSha256, api_sha256: savedScript.sha256, byte_length: Buffer.byteLength(source), sha256_matches: true, revision: savedScript.revision }, null, 2))

  const taskDefinition = {
    name: `${prefix}-task`, type: 'script', script_id: scriptId,
    target: { type: 'node', node_ids: [nodeId] },
    parameters: [{ name: 'qa_input', type: 'string', required: true, default: 'task-default' }],
    param_values: {}, timeout: 60, retry: 0, offline_policy: 'hub_online_required', run_user: '', enabled: true, schedule: null,
  }
  const taskCreate = await api('/tasks', 'POST', taskDefinition)
  if (![200, 201].includes(taskCreate.status) || typeof taskCreate.body?.id !== 'string') throw new Error(`Task setup HTTP ${taskCreate.status}`)
  taskId = taskCreate.body.id; ledger.task_id = taskId; await persist()
  const taskRead = await api(`/tasks/${taskId}`)
  if (taskRead.status !== 200) throw new Error(`Task readback HTTP ${taskRead.status}`)
  const savedTask = taskRead.body
  const taskMatch = savedTask.type === 'script' && savedTask.script_id === scriptId && savedTask.target?.type === 'node' && savedTask.target.node_ids?.length === 1 && savedTask.target.node_ids[0] === nodeId && (savedTask.target.group_ids || []).length === 0 && savedTask.retry === 0 && !savedTask.schedule && (savedTask.run_user || '') === '' && savedTask.parameters?.some(p => p.name === 'qa_input' && p.required && p.default === 'task-default')
  if (!taskMatch) throw new Error('Task API definition differs from exact singleton/no-schedule expectation')
  await writeFile(`${dir}/definition/task-api-readback.json`, JSON.stringify(savedTask, null, 2))
  await writeFile(`${dir}/definition/task-request.json`, JSON.stringify(taskDefinition, null, 2))
  await writeFile(`${dir}/definition/pre-run-state.json`, JSON.stringify({ source_sha256: sourceSha256, script_id: scriptId, script_revision: savedScript.revision, script_sha256: savedScript.sha256, task_id: taskId, task_revision: savedTask.revision, task_target_exact_singleton: true, task_has_no_group_or_schedule: true, selected_run_user: { username, uid }, expected }, null, 2))
  ledger.status = 'READY_FOR_UI_RUN'; ledger.pre_run_definition_verified = true
  await persist()
} catch (error) {
  ledger.status = 'SETUP_FAILED'; ledger.error = String(error?.message || error).slice(0, 300)
} finally {
  if (ledger.status !== 'READY_FOR_UI_RUN') {
    for (const [kind, id] of [['tasks', taskId], ['scripts', scriptId]]) {
      if (!id) continue
      const del = await api(`/${kind}/${id}`, 'DELETE'); const read = await api(`/${kind}/${id}`)
      ledger.cleanup.push({ kind, id, delete_http: del.status, verify_get_http: read.status, clean: [200, 204, 404].includes(del.status) && read.status === 404 })
    }
  }
  delete c.admin_password; delete c.token; token = undefined
  await persist()
  process.stdout.write(JSON.stringify({ status: ledger.status, script_id: scriptId || null, task_id: taskId || null, source_sha256: sourceSha256, error: ledger.error || null }) + '\n')
}
