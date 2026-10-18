import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import { chromium } from '/home/superc/code/nodesteer/web/node_modules/playwright/index.mjs'
const out='/home/superc/code/nodesteer/.e2e/runs/20260930-101945/main'
const cfg=JSON.parse(await fs.readFile('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json','utf8'))
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({baseURL:cfg.base_url,locale:'zh-CN'})
const page=await context.newPage()
const login=await context.request.post('/api/login',{data:{username:cfg.admin_username,password:cfg.admin_password}})
assert.equal(login.status(),200)
const token=(await login.json()).token,h={Authorization:'Bearer '+token}
const original=await(await context.request.get('/api/settings',{headers:h})).json()
const changed={...original,heartbeat_interval_sec:String(Number(original.heartbeat_interval_sec)+1)}
const result={case_id:'MAIN-SETTINGS-UI-EDIT-PERSIST-RESTORE',classification:'REAL_UI_API',status:'RUNNING',original,changed}
async function save(value){
  await page.goto('/settings')
  const input=page.getByRole('textbox',{name:'心跳间隔（秒）',exact:true});await input.waitFor();await input.fill(value)
  const response=page.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname==='/api/settings')
  await page.getByRole('button',{name:/^保存$/}).click();const r=await response;assert.equal(r.status(),200)
  await page.getByText('已保存',{exact:true}).waitFor()
  await page.reload();await input.waitFor();return input.inputValue()
}
try{
  await page.goto('/sign-in');await page.evaluate(t=>localStorage.setItem('nodesteer_token',t),token)
  result.changed_after_reload=await save(changed.heartbeat_interval_sec)
  result.api_changed=await(await context.request.get('/api/settings',{headers:h})).json()
  assert.deepEqual(result.api_changed,changed);assert.equal(result.changed_after_reload,changed.heartbeat_interval_sec)
  result.restored_after_reload=await save(original.heartbeat_interval_sec)
  result.api_restored=await(await context.request.get('/api/settings',{headers:h})).json()
  assert.deepEqual(result.api_restored,original);assert.equal(result.restored_after_reload,original.heartbeat_interval_sec)
  result.status='PASS'
}finally{
  const current=await(await context.request.get('/api/settings',{headers:h})).json()
  if(JSON.stringify(current)!==JSON.stringify(original))await context.request.put('/api/settings',{headers:h,data:original})
  await context.request.post('/api/logout',{headers:h});await browser.close()
  await fs.writeFile(out+'/settings-persistence-result.json',JSON.stringify(result,null,2)+'\n')
  process.stdout.write(JSON.stringify({case_id:result.case_id,status:result.status,changed_after_reload:result.changed_after_reload,restored_after_reload:result.restored_after_reload})+'\n')
}
