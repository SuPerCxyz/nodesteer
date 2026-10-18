import { readFile, writeFile } from 'node:fs/promises'
const dir = new URL('.', import.meta.url).pathname
const c = JSON.parse(await readFile('/tmp/nodesteer-e2e-resume-20261008-q0w7xmcw/repair-connection.json', 'utf8'))
const pre = JSON.parse(await readFile(`${dir}/definition/pre-run-state.json`, 'utf8'))
const oracle = JSON.parse(await readFile(`${dir}/definition/expected-oracle.json`, 'utf8'))
const ledger = JSON.parse(await readFile(`${dir}/ledger.json`, 'utf8'))
const base = 'http://192.168.100.209:18080'
const executionId = '337898e9-4037-48d7-bc48-d3015d6db180'
const checks = []
const login = await fetch(`${base}/api/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: c.admin_username, password: c.admin_password }) })
if (!login.ok) throw new Error(`repair auth HTTP ${login.status}`)
const loginBody = await login.json(); let token = loginBody.token || loginBody.Token
async function api(path) {
  const r = await fetch(`${base}/api${path}`, { headers: { Authorization: `Bearer ${token}` } })
  let body = null; try { body = await r.json() } catch {}
  checks.push({ method: 'GET', path, status: r.status })
  return { status: r.status, body }
}
const execution = await api(`/executions/${executionId}`)
const script = await api(`/scripts/${pre.script_id}`)
const task = await api(`/tasks/${pre.task_id}`)
const audit = await api('/audit?limit=500&offset=0')
const stdout = String(execution.body?.stdout || '')
const values = Object.fromEntries(stdout.split(/\r?\n/).filter(Boolean).map(line => { const i = line.indexOf('='); return i < 0 ? [line, ''] : [line.slice(0, i), line.slice(i + 1)] }))
const list = Array.isArray(audit.body) ? audit.body : audit.body?.items || []
const taskAudit = list.filter(x => x.resource === 'task' && x.resource_id === pre.task_id).map(x => ({ id: x.id, action: x.action, resource: x.resource, resource_id: x.resource_id }))
const scriptMatch = script.status === 200 && script.body.sha256 === pre.source_sha256 && script.body.working_dir === '/tmp' && script.body.run_user === oracle.expected.username
const taskMatch = task.status === 200 && task.body.script_id === pre.script_id && task.body.target?.type === 'node' && task.body.target.node_ids?.length === 1 && task.body.target.node_ids[0] === oracle.target_node_id && (task.body.target.group_ids || []).length === 0 && task.body.retry === 0 && !task.body.schedule && (task.body.run_user || '') === ''
const executionMatch = execution.status === 200 && execution.body.id === executionId && execution.body.task_id === pre.task_id && execution.body.script_id === pre.script_id && execution.body.node_id === oracle.target_node_id && execution.body.status === 'SUCCESS'
const oracleMatch = values.shell_cwd === '/tmp' && values.python_cwd === '/tmp' && values.uid === String(oracle.expected.uid) && values.python_uid === String(oracle.expected.uid) && values.username === oracle.expected.username && values.python_username === oracle.expected.username && values.parameter === oracle.expected.parameter && values.environment === oracle.expected.environment
const auditExecute = taskAudit.some(x => x.action === 'execute')
const result = {
  run: 'e2e03-runtime-20261008-7f3a1d2b',
  task_id: pre.task_id,
  script_id: pre.script_id,
  execution_id: executionId,
  requests: checks,
  execution: { id: execution.body?.id, task_id: execution.body?.task_id, script_id: execution.body?.script_id, script_revision: execution.body?.script_revision, task_revision: execution.body?.task_revision, node_id: execution.body?.node_id, trigger_type: execution.body?.trigger_type, status: execution.body?.status, exit_code: execution.body?.exit_code ?? null, exit_code_available: Object.hasOwn(execution.body || {}, 'exit_code') },
  actual_stdout_oracle: values,
  expected: oracle.expected,
  script_definition_matches_pre_run_source_sha: scriptMatch,
  task_definition_exact_singleton_no_group_retry0_no_schedule: taskMatch,
  execution_success_on_native3: executionMatch,
  cwd_run_user_parameter_environment_consumed: oracleMatch,
  task_audit_events_api: taskAudit,
  task_execute_audit_present: auditExecute,
  exit_code_reporting_observation: execution.body?.exit_code === undefined ? 'SUCCESS is reported but exit_code is omitted from API; UI displayed -.' : 'exit_code is present',
  status: scriptMatch && taskMatch && executionMatch && oracleMatch && auditExecute ? 'API_ORACLE_PASS' : 'API_ORACLE_FAIL',
  ui_logs_pending: true,
  ui_audit_pending: true,
}
await writeFile(`${dir}/result/api-execution-oracle.json`, JSON.stringify(result, null, 2))
ledger.execution_id = executionId; ledger.runtime_status = result.status
await writeFile(`${dir}/ledger.json`, JSON.stringify(ledger, null, 2))
delete c.admin_password; delete c.token; token = undefined
console.log(JSON.stringify({ status: result.status, execution_id: executionId, node_id: result.execution.node_id, status_value: result.execution.status, cwd: values.shell_cwd, uid: values.uid, username: values.username, parameter_match: values.parameter === oracle.expected.parameter, environment_match: values.environment === oracle.expected.environment, audit_execute: auditExecute }))
