(async () => {
  const token = localStorage.getItem('nodesteer_token')
  const auth = { Authorization: 'Bearer ' + token }
  const me = await fetch('/api/me', { headers: auth })
  const meBody = await me.json()
  const logout = await fetch('/api/logout', { method: 'POST', headers: auth })
  const logoutBody = await logout.json()
  const sameTokenMe = await fetch('/api/me', { headers: auth })
  const sameTokenMeStatus = sameTokenMe.status
  localStorage.setItem('nodesteer_token', 'qa-invalid-token-20261010-readonly-execution-auth')
  return JSON.stringify({
    pre_logout_me_status: me.status,
    role: meBody.Role || meBody.role || null,
    logout_status: logout.status,
    logout_ok: logoutBody.ok === 'true' || logoutBody.ok === true,
    same_token_me_after_logout_status: sameTokenMeStatus,
    invalid_token_written: localStorage.getItem('nodesteer_token') === 'qa-invalid-token-20261010-readonly-execution-auth',
    token_value_returned: false,
  })
})()
