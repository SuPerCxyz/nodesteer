// Real UI sequence on the transfers table:
//   1. wait for the batch-owned row (source path contains window.__b04Marker)
//   2. open its 操作 menu (radix dropdown needs a pointer event sequence, not .click())
//   3. click 取消传输 as soon as it appears
// Returns timings + the row text before/at/after the cancel so the server-side
// status at click time can be cross-checked against the polled timeline.
(() => {
  const marker = window.__b04Marker;
  const t0 = performance.now();
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const rowsOf = () => [...document.querySelectorAll('table tbody tr')];
  const seq = (el) => {
    const base = {
      bubbles: true, cancelable: true, composed: true, button: 0,
      buttons: 1, pointerId: 1, pointerType: 'mouse', isPrimary: true,
      clientX: 0, clientY: 0,
    };
    el.dispatchEvent(new PointerEvent('pointerdown', base));
    el.dispatchEvent(new MouseEvent('mousedown', base));
    el.dispatchEvent(new PointerEvent('pointerup', { ...base, buttons: 0 }));
    el.dispatchEvent(new MouseEvent('mouseup', { ...base, buttons: 0 }));
    el.dispatchEvent(new MouseEvent('click', { ...base, buttons: 0 }));
  };
  return (async () => {
    const out = { marker, steps: [] };
    let row = null;
    const deadline = performance.now() + 10000;
    while (performance.now() < deadline) {
      row = rowsOf().find((r) => (r.innerText || '').includes(marker));
      if (row) break;
      await sleep(10);
    }
    if (!row) return { ...out, ok: false, reason: 'row not found', elapsed_ms: Math.round(performance.now() - t0) };
    out.row_wait_ms = Math.round(performance.now() - t0);
    out.rowTextBefore = row.innerText.replace(/\s+/g, ' ').slice(0, 240);

    const menuBtn = [...row.querySelectorAll('button')].find(
      (b) =>
        (b.innerText || '').trim() === '操作' ||
        b.getAttribute('aria-label') === '操作' ||
        b.getAttribute('data-slot') === 'dropdown-menu-trigger'
    );
    if (!menuBtn) return { ...out, ok: false, reason: 'row menu button not found' };
    seq(menuBtn);
    let item = null;
    const d2 = performance.now() + 5000;
    while (performance.now() < d2) {
      item = [...document.querySelectorAll('[role=menuitem]')].find(
        (m) => (m.innerText || '').trim() === '取消传输'
      );
      if (item) break;
      await sleep(5);
    }
    const openItems = [...document.querySelectorAll('[role=menuitem]')].map((m) => (m.innerText || '').trim());
    if (!item) return { ...out, ok: false, reason: 'cancel menu item not found', menu_items: openItems };
    out.menu_items = openItems;
    out.menu_open_ms = Math.round(performance.now() - t0);

    // re-read the row text right before clicking (server status is polled separately)
    const fresh = rowsOf().find((r) => (r.innerText || '').includes(marker)) || row;
    out.rowTextAtCancel = fresh.innerText.replace(/\s+/g, ' ').slice(0, 240);
    seq(item);
    out.cancel_clicked_ms_after_start = Math.round(performance.now() - t0);

    // the product design has no confirmation dialog for 取消传输: confirm that no
    // dialog appeared within a short window after the click.
    let dialogsSeen = 0;
    for (let i = 0; i < 30; i++) {
      dialogsSeen = Math.max(
        dialogsSeen,
        document.querySelectorAll('[role=alertdialog],[role=dialog]').length
      );
      await sleep(10);
    }
    out.dialogs_present_after_click = dialogsSeen;

    const after = rowsOf().find((r) => (r.innerText || '').includes(marker));
    out.rowTextAfter = after ? after.innerText.replace(/\s+/g, ' ').slice(0, 240) : null;
    out.row_still_present_after_cancel = Boolean(after);
    out.ok = true;
    out.total_ms = Math.round(performance.now() - t0);
    return out;
  })();
})()
