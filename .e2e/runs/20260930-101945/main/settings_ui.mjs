import fs from 'node:fs/promises'
import assert from 'node:assert/strict'
import { chromium } from '/home/superc/code/nodesteer/web/node_modules/playwright/index.mjs'

const privateDir='/tmp/nodesteer-e2e-20260930-101945-7wksl0_x'
const out='/home/superc/code/nodesteer/.e2e/runs/20260930-101945/main'
const cfg=JSON.parse(await fs.readFile(privateDir+'/connection.json','utf8'))
const browser=await chromium.launch({headless:true})
const context=await browser.newContext({baseURL:cfg.base_url,locale:'zh-CN'})
const page=await context.newPage()
const rows=[]
let token
let original
const labels={heartbeat_interval_sec:'心跳间隔（秒）',revision_check_interval_sec:'修订检查间隔（秒）',changelog_window:'变更日志窗口',max_log_bytes:'最大日志字节数'}
async function api(method,path,data){
  const r=await context.request.fetch(path,{method,headers:{Authorization:'Bearer '+token},data})
  return {http:r.status(),body:await r.json()}
}
async function record(id,ok,detail){
  rows.push({case_id:id,classification:'REAL_UI_API',status:ok?'PASS':'FAIL',detail})
  await fs.writeFile(out+'/settings-ui-results.json',JSON.stringify(rows,null,2)+'\n')
  process.stdout.write(id+' '+rows.at(-1).status+'\n')
}
try{
  const login=await context.request.post('/api/login',{data:{username:cfg.admin_username,password:cfg.admin_password}})
  assert.equal(login.status(),200);token=(await login.json()).token
  await page.goto('/sign-in');await page.evaluate(t=>localStorage.setItem('nodesteer_token',t),token)
  original=(await api('GET','/api/settings')).body
  assert.equal(Object.keys(original).length,4)
  const save=page.getByRole('button',{name:/^保存$/})
  for(const [key,label] of Object.entries(labels)){
    for(const value of ['not-a-number','0','-1','999999999999999999999999']){
      await page.goto('/settings');await page.getByRole('textbox',{name:label,exact:true}).waitFor()
      await page.getByRole('textbox',{name:label,exact:true}).fill(value)
      const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/settings'&&r.request().method()==='PUT')
      await save.click();const r=await response;const error=await r.json()
      await page.getByText(error.error,{exact:true}).waitFor()
      const current=await api('GET','/api/settings')
      await record('MAIN-SETTINGS-UI-INVALID-'+key+'-'+value,r.status()===400&&JSON.stringify(current.body)===JSON.stringify(original),{key,input:value,http:r.status(),error:error.error,ui_error_visible:true,original_unchanged:JSON.stringify(current.body)===JSON.stringify(original)})
    }
  }
  for(let i=0;i<2;i++){
    await page.goto('/settings')
    await page.getByRole('textbox',{name:labels.heartbeat_interval_sec,exact:true}).waitFor()
    await page.getByRole('textbox',{name:labels.heartbeat_interval_sec,exact:true}).fill(original.heartbeat_interval_sec)
    const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/settings'&&r.request().method()==='PUT')
    await save.click();const r=await response
    await page.getByText('已保存',{exact:true}).waitFor()
    await page.reload();const current=await api('GET','/api/settings')
    const values=await page.locator('main input').evaluateAll(x=>x.map(e=>e.value).sort())
    await record('MAIN-SETTINGS-UI-SAVE-REFRESH-'+i,r.status()===200&&JSON.stringify(current.body)===JSON.stringify(original)&&JSON.stringify(values)===JSON.stringify(Object.values(original).sort()),{http:r.status(),success_feedback:true,api_original_unchanged:true,refreshed_values:values})
  }
  await page.route('**/api/settings',async route=>{
    if(route.request().method()==='PUT')await new Promise(resolve=>setTimeout(resolve,600))
    await route.continue()
  })
  let writes=0
  page.on('request',r=>{if(new URL(r.url()).pathname==='/api/settings'&&r.method()==='PUT')writes++})
  const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/settings'&&r.request().method()==='PUT')
  await save.dblclick();const pendingDisabled=await save.isDisabled();const r=await response
  await page.waitForTimeout(800)
  await record('MAIN-SETTINGS-UI-PENDING-DUPLICATE',pendingDisabled&&writes===1&&r.status()===200,{controlled_put_delay_ms:600,actual_http:r.status(),pending_disabled:pendingDisabled,actual_put_requests:writes,values_unchanged:JSON.stringify((await api('GET','/api/settings')).body)===JSON.stringify(original)})
}catch(error){
  await record('MAIN-SETTINGS-UI-HARNESS',false,{error_name:error.name,error_message:error.message})
  process.exitCode=1
}finally{
  if(token&&original){
    const current=await api('GET','/api/settings')
    if(JSON.stringify(current.body)!==JSON.stringify(original))await api('PUT','/api/settings',original)
    await record('MAIN-SETTINGS-UI-FINAL-BASELINE',JSON.stringify((await api('GET','/api/settings')).body)===JSON.stringify(original),{original,actual:(await api('GET','/api/settings')).body,gateway_key_added:false})
    await context.request.post('/api/logout',{headers:{Authorization:'Bearer '+token}})
  }
  await browser.close()
}
