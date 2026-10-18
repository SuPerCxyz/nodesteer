(() => {
  const main = document.querySelector('main');
  const anchors = [...document.querySelectorAll('a')].map(a => a.getAttribute('href')).filter(h => h && h.includes('/new'));
  const buttons = [...document.querySelectorAll('button')].map(b => b.textContent.trim()).filter(Boolean);
  const rows = [...document.querySelectorAll('table tbody tr')];
  const rowActionButtons = rows.map(r => [...r.querySelectorAll('button')].map(b => b.textContent.trim()).filter(Boolean));
  return {
    url: location.pathname,
    createAnchors: anchors,
    hasUploadControl: document.querySelectorAll('input[type=file]').length,
    hasEditDelete: buttons.filter(t => /^(编辑|删除|取消|添加|保存)$/.test(t)),
    rowCount: rows.length,
    rowActionButtons,
    mainText: (main?.innerText || '').trim().slice(0, 120),
  };
})()
