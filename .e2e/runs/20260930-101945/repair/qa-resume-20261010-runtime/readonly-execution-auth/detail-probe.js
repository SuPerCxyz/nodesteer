(async () => {
  const id = 'e7a4ce07-4a10-420c-909f-f2e96a91738d'
  const h = { Authorization: 'Bearer ' + localStorage.getItem('nodesteer_token') }
  const [er, tr, lr] = await Promise.all([
    fetch('/api/executions/' + id, { headers: h }),
    fetch('/api/tasks/61709c81-66b6-4e0f-8c5b-a969b721b222', { headers: h }),
    fetch('/api/executions/' + id + '/logs', { headers: h }),
  ])
  const e = await er.json()
  const l = lr.ok ? await lr.json() : null
  const text = document.body.innerText
  return JSON.stringify({
    url: location.href,
    api: {
      execution_status: er.status,
      task_status: tr.status,
      logs_status: lr.status,
      execution: {
        id: e.id, task_id: e.task_id, node_id: e.node_id, status: e.status,
        trigger_type: e.trigger_type, task_revision: e.task_revision,
        has_script_id: Object.hasOwn(e, 'script_id'),
        has_script_revision: Object.hasOwn(e, 'script_revision'),
      },
      logs_count: Array.isArray(l) ? l.length : null,
      log_streams: Array.isArray(l) ? [...new Set(l.map(x => x.stream))] : [],
    },
    ui: {
      execution_id_visible: text.includes(id),
      metadata_section: text.includes('元数据'),
      logs_section: text.includes('日志'),
      same_document_logs: !!document.querySelector('#execution-logs'),
      log_pre_count: document.querySelectorAll('#execution-logs pre').length,
      tab_count: document.querySelectorAll('[role=tab]').length,
      task_revision_visible: text.includes('r' + e.task_revision),
      script_revision_visible: /脚本修订|Script Revision/.test(text),
      status: e.status,
    },
  })
})()
