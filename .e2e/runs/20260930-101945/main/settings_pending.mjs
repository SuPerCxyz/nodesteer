import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import { chromium } from '/home/superc/code/nodesteer/web/node_modules/playwright/index.mjs'
const out='/home/superc/code/nodesteer/.e2e/runs/20260930-101945/main'
const cfg=JSON.parse(await fs.readFile('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json','utf8'))
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({baseURL:cfg.base_url,locale:'zh-CN'})
const page=await context.newPage()
const events=[]
const login=await context.request.post('/api/login',{data:{username:cfg.admin_username,password:cfg.admin_password}})
assert.equal(login.status(),200)
const token=(await login.json()).token
const h={Authorization:'Bearer '+token}
const original=await(await context.request.get('/api/settings',{headers:h})).json()
try{
  await page.goto('/sign-in');await page.evaluate(t=>localStorage.setItem('nodesteer_token',t),token)
  await page.goto('/settings');const input=page.getByRole('textbox',{name:'心跳间隔（秒）',exact:true});await input.waitFor();await input.fill(original.heartbeat_interval_sec)
  await page.route('**/api/settings',async route=>{if(route.request().method()==='PUT')await new Promise(r=>setTimeout(r,1500));await route.continue()})
  page.on('request',r=>{if(r.method()==='PUT'&&new URL(r.url()).pathname==='/api/settings')events.push({event:'PUT_request',time:Date.now()})})
  page.on('response',r=>{if(r.request().method()==='PUT'&&new URL(r.url()).pathname==='/api/settings')events.push({event:'PUT_response',time:Date.now(),http:r.status()})})
  const button=page.getByRole('button',{name:/^保存$/});const box=await button.boundingBox();assert.ok(box)
  await button.evaluate(b=>{window.qaSettingsClicks=[];b.addEventListener('click',e=>window.qaSettingsClicks.push({event:'button_click',time:Date.now(),trusted:e.isTrusted,disabled:b.disabled}))})
  const response=page.waitForResponse(r=>r.request().method()==='PUT'&&new URL(r.url()).pathname==='/api/settings')
  await page.mouse.click(box.x+box.width/2,box.y+box.height/2)
  await page.waitForTimeout(100)
  const disabledDuringRequest=await button.isDisabled()
  events.push({event:'pending_sample',time:Date.now(),disabled:disabledDuringRequest})
  await page.mouse.click(box.x+box.width/2,box.y+box.height/2)
  await response;await page.waitForTimeout(1800)
  const clicks=await page.evaluate(()=>window.qaSettingsClicks)
  const current=await(await context.request.get('/api/settings',{headers:h})).json()
  const row={case_id:'MAIN-SETTINGS-UI-PENDING-DUPLICATE',classification:'REAL_UI_API',status:disabledDuringRequest&&events.filter(e=>e.event==='PUT_request').length===1?'PASS':'FAIL',detail:{controlled_put_delay_ms:1500,second_physical_click_after_ms:100,disabledDuringRequest,events,clicks,original_unchanged:JSON.stringify(current)===JSON.stringify(original)}}
  await fs.writeFile(out+'/settings-pending-retest.json',JSON.stringify(row,null,2)+'\n')
  const rows=JSON.parse(await fs.readFile(out+'/settings-ui-results.json','utf8'))
  await fs.writeFile(out+'/settings-ui-pending-first-attempt.json',JSON.stringify(rows.find(x=>x.case_id===row.case_id),null,2)+'\n')
  rows[rows.findIndex(x=>x.case_id===row.case_id)]=row
  await fs.writeFile(out+'/settings-ui-results.json',JSON.stringify(rows,null,2)+'\n')
  process.stdout.write(JSON.stringify(row)+'\n')
}finally{
  const current=await(await context.request.get('/api/settings',{headers:h})).json()
  if(JSON.stringify(current)!==JSON.stringify(original))await context.request.put('/api/settings',{headers:h,data:original})
  await context.request.post('/api/logout',{headers:h});await browser.close()
}
