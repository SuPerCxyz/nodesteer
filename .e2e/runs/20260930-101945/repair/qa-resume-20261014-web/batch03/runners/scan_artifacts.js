(() => {
  const q = (s) => Array.from(document.querySelectorAll(s));
  const table = document.querySelector('table');
  const headers = table
    ? q('table thead th').map((th) => ({ text: th.innerText.trim(), ariaSort: th.getAttribute('aria-sort') }))
    : [];
  const rows = table
    ? q('table tbody tr').map((tr) => Array.from(tr.cells).map((c) => c.innerText.trim()))
    : [];
  const buttons = q('button').map((b) => {
    const sr = b.querySelector('.sr-only');
    return { sr: sr ? sr.textContent.trim() : '', text: b.innerText.trim().replace(/\s+/g, ' ').slice(0, 40), disabled: b.disabled, aria: b.getAttribute('aria-label') };
  }).filter((b) => b.sr || b.aria);
  const combos = q('[role="combobox"]').map((s) => ({ text: s.innerText.trim(), value: s.value || null }));
  const storage = {};
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k && k.startsWith('nodesteer.table.')) storage[k] = localStorage.getItem(k);
  }
  const bodyText = document.body.innerText;
  return {
    path: location.pathname + location.search,
    headers,
    rowCount: rows.length,
    rows: rows.map((r) => [r[0], r[1], r[2], r[4]]),
    buttons,
    combos,
    pageOf: (bodyText.match(/第 \d+ 页，共 \d+ 页/g) || []),
    searchValue: (q('input[type="search"], input[placeholder]')[0] || {}).value || null,
    storage,
  };
})()
