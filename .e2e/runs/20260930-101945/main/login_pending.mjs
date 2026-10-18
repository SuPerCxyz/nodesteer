import fs from 'node:fs/promises'
import { chromium } from '/home/superc/code/nodesteer/web/node_modules/playwright/index.mjs'
const out='/home/superc/code/nodesteer/.e2e/runs/20260930-101945/main'
const cfg=JSON.parse(await fs.readFile('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json','utf8'))
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({baseURL:cfg.base_url,locale:'zh-CN'})
const page=await context.newPage()
const events=[]
let result={case_id:'MAIN-LOGIN-PENDING-DUPLICATE',classification:'REAL_UI_API',status:'RUNNING'}
try{
  await page.goto('/sign-in');await page.getByLabel(/^用户名$/).fill(cfg.admin_username);await page.getByLabel(/^密码$/).fill(cfg.admin_password)
  await page.route('**/api/login',async route=>{await new Promise(r=>setTimeout(r,1500));await route.continue()})
  page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/login')events.push({event:'POST_login',time:Date.now()})})
  const button=page.locator('form button[type="submit"]');const box=await button.boundingBox()
  const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/login'&&r.request().method()==='POST')
  await page.mouse.click(box.x+box.width/2,box.y+box.height/2);await page.waitForTimeout(100)
  const disabled=await button.isDisabled();events.push({event:'pending_sample',disabled,time:Date.now()})
  await page.mouse.click(box.x+box.width/2,box.y+box.height/2)
  const r=await response;await page.waitForURL(cfg.base_url+'/')
  const actor=await page.evaluate(async()=>{const r=await fetch('/api/me',{headers:{Authorization:'Bearer '+localStorage.getItem('nodesteer_token')}});const d=await r.json();return {http:r.status,role:d.Role}})
  result={...result,status:disabled&&events.filter(e=>e.event==='POST_login').length===1&&r.status()===200&&actor.http===200?'PASS':'FAIL',detail:{controlled_login_delay_ms:1500,second_physical_click_after_ms:100,pending_disabled:disabled,events,login_http:r.status(),dashboard_path:new URL(page.url()).pathname,actor}}
  await page.evaluate(async()=>{const t=localStorage.getItem('nodesteer_token');await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}});localStorage.removeItem('nodesteer_token')})
}catch(error){
  result={...result,status:'HARNESS_ERROR',detail:{error_name:error.name,error_message:error.message}}
  process.exitCode=1
}finally{
  await page.evaluate(async()=>{const t=localStorage.getItem('nodesteer_token');if(t){await fetch('/api/logout',{method:'POST',headers:{Authorization:'Bearer '+t}});localStorage.removeItem('nodesteer_token')}}).catch(()=>{})
  await browser.close();await fs.writeFile(out+'/login-pending-result.json',JSON.stringify(result,null,2)+'\n');process.stdout.write(JSON.stringify(result)+'\n')
}
