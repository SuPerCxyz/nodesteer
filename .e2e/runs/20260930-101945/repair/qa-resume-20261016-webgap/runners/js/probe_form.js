(() => {
  const out = {url: location.pathname, headings: [], controls: [], texts: []};
  document.querySelectorAll('h1,h2,h3').forEach(h => out.headings.push(h.innerText.trim()));
  document.querySelectorAll('input,select,textarea,button').forEach(e => {
    out.controls.push({
      tag: e.tagName, type: e.type, name: e.name,
      aria: e.getAttribute('aria-label'), ph: e.placeholder,
      val: (e.value || '').slice(0, 50), dis: e.disabled,
      text: (e.innerText || '').trim().slice(0, 30)
    });
  });
  document.querySelectorAll('p,span.font-mono,label').forEach(e => {
    const t = (e.innerText || '').trim();
    if (t && t.length < 160) out.texts.push(t.slice(0, 160));
  });
  out.texts = [...new Set(out.texts)].slice(0, 60);
  return out;
})()
