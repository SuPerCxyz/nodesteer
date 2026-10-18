(() => {
  if (window.__nodesteerPendingUIObserver) return;
  const state = { events: [] };
  window.__nodesteerPendingUIObserver = state;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    try {
      const input = args[0];
      const rawURL = typeof input === 'string' ? input : input?.url;
      const url = new URL(rawURL, location.href);
      if (url.origin !== location.origin) return response;
      const executionMatch = url.pathname.match(/^\/api\/executions\/([0-9a-f-]{36})$/i);
      const taskMatch = url.pathname.match(/^\/api\/tasks\/([0-9a-f-]{36})$/i);
      const expectedTask = sessionStorage.getItem('qa_pending_ui_task_id');
      if (url.pathname !== '/api/me' && !executionMatch && !(taskMatch && taskMatch[1] === expectedTask)) return response;
      const payload = await response.clone().json();
      const event = { at_utc: new Date().toISOString(), path: url.pathname, http_status: response.status };
      if (url.pathname === '/api/me') {
        event.role = payload?.Role ?? payload?.role ?? null;
        event.role_is_administrator = event.role === 'administrator';
      } else if (executionMatch) {
        event.id = payload?.id ?? null;
        event.task_id = payload?.task_id ?? null;
        event.node_id = payload?.node_id ?? null;
        event.status = payload?.status ?? null;
      } else {
        const target = payload?.target ?? {};
        event.task_id = payload?.id ?? taskMatch[1];
        event.name = payload?.name ?? null;
        event.type = payload?.type ?? null;
        event.command = payload?.command ?? null;
        event.retry = payload?.retry ?? null;
        event.timeout = payload?.timeout ?? null;
        event.enabled = payload?.enabled ?? null;
        event.node_ids = target.node_ids ?? target.nodeIds ?? [];
        event.parameters = payload?.parameters ?? [];
      }
      state.events.push(event);
    } catch {
      // Never capture raw bodies, request headers, tokens, or non-target API responses.
    }
    return response;
  };
})();
