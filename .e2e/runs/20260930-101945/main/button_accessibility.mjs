import fs from 'node:fs/promises'
import { chromium } from '/home/superc/code/nodesteer/web/node_modules/playwright/index.mjs'
const out='/home/superc/code/nodesteer/.e2e/runs/20260930-101945/main'
const cfg=JSON.parse(await fs.readFile('/tmp/nodesteer-e2e-20260930-101945-7wksl0_x/connection.json','utf8'))
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({baseURL:cfg.base_url,locale:'zh-CN'})
const page=await context.newPage()
const login=await context.request.post('/api/login',{data:{username:cfg.admin_username,password:cfg.admin_password}})
if(login.status()!==200)throw new Error('fresh QA login failed')
const token=(await login.json()).token
const cdp=await context.newCDPSession(page)
const rows=[]
try{
  await page.goto('/sign-in');await page.evaluate(t=>localStorage.setItem('nodesteer_token',t),token)
  const paths=['/','/agents','/groups','/scripts','/tasks','/schedules','/applications','/artifacts','/executions','/transfers','/audit','/users','/settings','/groups/new','/scripts/new','/tasks/new','/schedules/new','/applications/new','/artifacts/new']
  for(const path of paths){
    await page.goto(path);await page.waitForLoadState('networkidle')
    const tree=await cdp.send('Accessibility.getFullAXTree')
    const buttons=tree.nodes.filter(n=>!n.ignored&&n.role?.value==='button')
    const unnamed=buttons.filter(n=>!String(n.name?.value||'').trim())
    rows.push({path,actual_url:new URL(page.url()).pathname,accessible_button_count:buttons.length,unnamed_buttons:unnamed.map(n=>({backend_dom_node_id:n.backendDOMNodeId,role:n.role.value,name:n.name?.value||'',description:n.description?.value||''})),status:unnamed.length?'FAIL':'PASS'})
    await fs.writeFile(out+'/button-accessibility-results.json',JSON.stringify({case_id:'MAIN-BUTTON-ACCESSIBLE-NAMES',classification:'REAL_UI_READONLY',status:rows.some(r=>r.status==='FAIL')?'FAIL':'PASS',scope:'19基础列表/新建页现有可访问树按钮名称；详情/动态弹窗另由Web补验，不以此泛称所有控件完整',pages:rows},null,2)+'\n')
  }
  process.stdout.write(JSON.stringify({pages:rows.length,failed:rows.filter(r=>r.status==='FAIL').map(r=>({path:r.path,unnamed:r.unnamed_buttons.length}))})+'\n')
}finally{
  await context.request.post('/api/logout',{headers:{Authorization:'Bearer '+token}})
  await browser.close()
}
