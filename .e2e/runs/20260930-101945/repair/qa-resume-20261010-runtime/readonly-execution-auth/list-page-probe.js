(async () => {
  const u = new URL(location.href), p = u.searchParams
  const offset = Number(window.__qaPageOffset || 0)
  const q = new URLSearchParams({ limit: '10', offset: String(offset) })
  if (p.get('status')) q.set('status', p.get('status'))
  if (p.get('q')) q.set('q', p.get('q'))
  if (p.get('trigger')) q.set('trigger_type', p.get('trigger'))
  const r = await fetch('/api/executions?' + q, {
    headers: { Authorization: 'Bearer ' + localStorage.getItem('nodesteer_token') },
  })
  const d = await r.json()
  const statusLabels = ['等待中','运行中','成功','失败','已跳过','已取消','超时','已阻断']
  const ui = [...document.querySelectorAll('tbody tr')].map(tr => {
    const txt = [...tr.querySelectorAll('td')].map(td => td.innerText.trim())
    const paths = [...tr.querySelectorAll('a[href]')].map(a => new URL(a.href).pathname)
    return {
      id: paths.map(x => x.startsWith('/executions/') ? x.split('/')[2] : null).find(Boolean) || null,
      node_id: paths.map(x => x.startsWith('/agents/') ? x.split('/')[2] : null).find(Boolean) || null,
      task_link_present: paths.some(x => x.startsWith('/tasks/')),
      status: statusLabels.find(x => txt.includes(x)) || null,
      trigger: txt.find(x => ['手动','调度','系统'].includes(x)) || null,
    }
  })
  return JSON.stringify({
    url: location.href,
    query: { q: p.get('q') || '', status: p.get('status') || '', trigger_type: p.get('trigger') || '' },
    headers: [...document.querySelectorAll('thead th')].map(x => x.innerText.trim()),
    api_status: r.status,
    total: Number(r.headers.get('X-Total-Count') || 0),
    api_ids: (d || []).map(x => x.id),
    api_statuses: (d || []).map(x => x.status),
    api_triggers: (d || []).map(x => x.trigger_type),
    ui,
    pagination_labels: [...document.querySelectorAll('button')].map(b => b.textContent.trim()).filter(x => /第一页|上一页|第 [0-9]+ 页|下一页|最后一页/.test(x)),
  })
})()
