(() => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const table = document.querySelector('table');
  const headers = table ? q('table thead th').map((th) => ({ text: th.innerText.trim(), ariaSort: th.getAttribute('aria-sort') })) : [];
  const rows = table ? q('table tbody tr').map((tr) => q('tr').length && Array.from(tr.cells).map((c) => c.innerText.trim())) : [];
  const pag = q('button').filter((b) => (b.getAttribute('aria-label') || '').match(/页|Page|page/)).map((b) => ({ label: b.getAttribute('aria-label'), srOnly: (b.querySelector('.sr-only') || {}).textContent || '', disabled: b.disabled, text: b.innerText.trim() }));
  const pageBtns = q('button[class*="min-w-8"]').map((b) => ({ text: b.innerText.trim(), disabled: b.disabled, variant: b.className.includes('bg-primary') ? 'current' : 'outline' }));
  const selectTriggers = q('[role="combobox"], select').map((s) => ({ role: s.getAttribute('role'), text: s.innerText.trim(), value: s.value || null }));
  const search = q('input[type="search"], input[placeholder]').map((i) => ({ placeholder: i.placeholder, value: i.value, type: i.type }));
  const bodyText = document.body.innerText;
  const pageOf = (bodyText.match(/第 \d+ 页[^\n]*/g) || []).slice(0, 4);
  const rowsPerPageLabel = (bodyText.match(/每页[^\n]*/g) || []).slice(0, 2);
  return {
    path: location.pathname + location.search,
    tableCount: q('table').length,
    headers,
    rowCount: rows.length,
    rowSample: rows.slice(0, 14),
    paginationButtons: pag,
    pageNumberButtons: pageBtns,
    selectTriggers,
    search,
    pageOf,
    rowsPerPageLabel,
    emptyRowText: table && rows.length === 1 && rows[0].length === 1 ? rows[0][0] : null,
    storage: { tasks: localStorage.getItem('nodesteer.table.tasks'), applications: localStorage.getItem('nodesteer.table.applications'), artifacts: localStorage.getItem('nodesteer.table.artifacts'), schedules: localStorage.getItem('nodesteer.table.schedules') }
  };
})()
