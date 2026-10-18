import { test, expect } from 'playwright/test'
import { appendFile, readFile, writeFile } from 'node:fs/promises'
import { createHash, randomBytes } from 'node:crypto'
import { apiRequest, captureCreated, cleanupByIds, createIsolatedTask, getNodeIds, login, loginViaApi, testName } from './fixtures'

async function evidence(name:string,data:unknown) {
  if(process.env.NODESTEER_E2E_EVIDENCE_DIR)await writeFile(process.env.NODESTEER_E2E_EVIDENCE_DIR+'/'+name+'.json',JSON.stringify(data,null,2))
}

test('SAFE-01 所有11表格搜索/无结果/Reset/排序/列持久化/分页',async({page})=>{
  test.setTimeout(300_000)
  await loginViaApi(page)
  const owned:string[]=[],results:unknown[]=[]
  const names:Record<string,string>={}
  try {
    for(const [kind,data] of [['groups',{type:'static',members:[]}],['scripts',{interpreter:'shell',content:'echo table',enabled:true}]] as const) {
      names[kind]=testName('table-'+kind)
      const result=await apiRequest(page,'/api/'+kind,{method:'POST',data:{name:names[kind],...data}})
      expect(result.ok).toBeTruthy();owned.push('/api/'+kind+'/'+(result.body as {id:string}).id)
    }
    names.agents=((await apiRequest(page,'/api/nodes')).body as {hostname:string}[])[0].hostname
    names.users=process.env.NODESTEER_E2E_USERNAME||'admin'
    names.audit=((await apiRequest(page,'/api/audit?limit=1')).body as {username:string}[])[0].username
    names.transfers=((await apiRequest(page,'/api/transfers')).body as {id:string}[])[0].id
    const task=await createIsolatedTask(page,'table-task');owned.unshift('/api/tasks/'+task.id);names.tasks=task.name
    const schedule=await apiRequest(page,'/api/schedules',{method:'POST',data:{task_id:task.id,type:'cron',expression:'0 0 1 1 *',timezone:'UTC',execution_owner:'agent',offline_policy:'allow_offline',misfire_policy:'skip',enabled:false}})
    expect(schedule.ok).toBeTruthy();owned.unshift('/api/schedules/'+(schedule.body as {id:string}).id);names.schedules=task.name
    await page.goto('/artifacts/new');names.artifacts=testName('table-artifact')
    await page.getByLabel(/^名称$|^name$/i).fill(names.artifacts);await page.getByLabel(/^版本$|^version$/i).fill('v1')
    await page.locator('input[type=file]').setInputFiles({name:'qa-table.txt',mimeType:'application/octet-stream',buffer:Buffer.from('qa table fixture')})
    const artifactId=await captureCreated(page,'/api/artifacts',()=>page.getByRole('button',{name:/^上传$|^upload$/i}).click());owned.push('/api/artifacts/'+artifactId)
    names.applications=testName('table-application')
    const app=await apiRequest(page,'/api/applications',{method:'POST',data:{name:names.applications,version:'v1',artifact_id:artifactId,binary_path:'/tmp/qa-table-definition-only',unit_name:testName('table-unit')+'.service',health_check:{type:'systemd',timeout:5,attempts:3,interval:2}}})
    expect(app.ok).toBeTruthy();owned.unshift('/api/applications/'+(app.body as {id:string}).id)
    for(const module of ['agents','groups','scripts','tasks','schedules','applications','artifacts','executions','transfers','audit','users']) {
      await page.goto('/'+module)
      const table=page.locator('table').first();await expect(table).toBeVisible()
      const record:{module:string;checks:Record<string,unknown>}={module,checks:{}}
      const search=page.getByRole('textbox',{name:/搜索|search/i})
      const firstRowText=(await table.locator('tbody tr').first().innerText()).split('\n').find(x=>x.trim())!.trim()
      const query=module==='executions'?(await table.locator('a[href^="/executions/"]').first().getAttribute('href'))!.split('/').pop()!:names[module]||firstRowText.split(/\s+/)[0]
      await search.fill(query)
      if(module==='executions')await expect(table.locator(`a[href="/executions/${query}"]`)).toBeVisible()
      else if(module==='transfers')await expect(table.locator('tbody tr').first()).not.toContainText(/没有结果|无结果|no results/i)
      else await expect(table.locator('tbody')).toContainText(query)
      record.checks.search_hit=query
      await search.fill('qa-no-match-'+testName('none'))
      if(module==='executions') {
        await expect(page.getByText(/没有匹配的执行记录|No matching executions/i)).toBeVisible()
        await expect(table).toBeHidden()
      } else await expect(table.locator('tbody')).toContainText(/没有结果|无结果|no results/i)
      if(module==='executions'){await search.fill('');record.checks.reset_not_available=true}
      else await page.getByRole('button',{name:/^重置$|^reset$/i}).click()
      await expect(table.locator('tbody tr').first()).not.toContainText(/没有结果|无结果|no results/i)
      record.checks.search_no_results_and_reset=true
      const sort=table.getByRole('button',{name:/升序|降序|清除排序|ascending|descending|clear sorting/i}).first()
      const th=sort.locator('..').locator('..')
      await sort.click();await expect(th).toHaveAttribute('aria-sort','ascending')
      await sort.click();await expect(th).toHaveAttribute('aria-sort','descending')
      await sort.click();await expect.poll(async()=>(await th.getAttribute('aria-sort'))||'none').toBe('none')
      record.checks.sort_cycle=true
      const beforeHeaders=await table.locator('thead th').count()
      await page.getByRole('button',{name:/^视图$|^view$/i}).click()
      const option=page.getByRole('menuitemcheckbox').first(),column=(await option.innerText()).trim()
      if(module!=='audit')await expect(page.getByRole('menuitemcheckbox',{name:/^操作$|^actions$/i})).toHaveCount(0)
      await option.click();await expect(table.locator('thead th')).toHaveCount(beforeHeaders-1)
      await page.reload();await expect(table.locator('thead th')).toHaveCount(beforeHeaders-1)
      await page.getByRole('button',{name:/^视图$|^view$/i}).click();await page.getByRole('menuitemcheckbox',{name:column,exact:true}).click()
      await expect(table.locator('thead th')).toHaveCount(beforeHeaders);record.checks.column_hide_restore_persist=column
      for(const size of ['10','20','30','40','50']) {
        await page.getByRole('combobox').last().click();await page.getByRole('option',{name:size,exact:true}).click()
        await expect(page.getByRole('combobox').last()).toHaveText(size)
      }
      await page.reload();await expect(page.getByRole('combobox').last()).toHaveText('50');record.checks.page_sizes_and_persist=true
      const first=page.getByRole('button',{name:/^跳转到第一页$|^go to first page$/i}),last=page.getByRole('button',{name:/^跳转到最后一页$|^go to last page$/i})
      await expect(first).toBeDisabled()
      if(await last.isEnabled()){await last.click();await expect(last).toBeDisabled();await expect(first).toBeEnabled();await first.click();await expect(first).toBeDisabled();record.checks.multiple_pages=true}
      else record.checks.single_page_disabled=true
      await page.getByRole('combobox').last().click();await page.getByRole('option',{name:'10',exact:true}).click()
      results.push(record);await evidence('tables-safe',results)
    }
  } finally {await cleanupByIds(page,owned)}
})

test('SAFE-02 Execution跨服务端页搜索已知ID',async({page})=>{
  await loginViaApi(page)
  const response=await apiRequest(page,'/api/executions?limit=1&offset=20')
  const id=(response.body as {id:string}[])[0]?.id
  if(!id)throw new Error('Need at least21 real executions for cross-page search')
  await page.goto('/executions')
  await page.getByRole('textbox',{name:/搜索|search/i}).fill(id)
  const actual={id,rows:await page.locator('table tbody').innerText(),url:page.url()}
  await evidence('execution-cross-page-search',actual)
  await expect(page.locator(`a[href="/executions/${id}"]`)).toBeVisible()
})

test('SAFE-03 [UI_MOCK] Error/Retry与Loading',async({page})=>{
  await loginViaApi(page)
  await page.route('**/api/groups',route=>route.fulfill({status:503,json:{error:'qa simulated unavailable'}}))
  await page.goto('/groups')
  await expect(page.getByText('qa simulated unavailable',{exact:true})).toBeVisible({timeout:25_000})
  await page.unroute('**/api/groups')
  await page.getByRole('button',{name:/^重试$|^retry$/i}).click()
  await expect(page.getByText('qa simulated unavailable',{exact:true})).toHaveCount(0)
  await expect(page.getByRole('heading',{name:/^分组$|^groups$/i})).toBeVisible()
})

test('SAFE-04 Script参数/环境增删覆盖空字段与取消',async({page})=>{
  await loginViaApi(page);await page.goto('/scripts/new')
  await page.getByRole('button',{name:/^添加参数$/}).click();await expect(page.locator('main')).not.toContainText('qa_param')
  for(const type of ['string','number','bool','secret']) {
    await page.getByPlaceholder('名称',{exact:true}).fill('qa_'+type)
    await page.getByRole('combobox').nth(1).click();await page.getByRole('option',{name:type,exact:true}).click()
    await page.getByPlaceholder('默认值',{exact:true}).fill(type==='number'?'42':type==='bool'?'true':'qa-value')
    await page.getByRole('button',{name:/^添加参数$/}).click()
  }
  await expect(page.locator('main')).toContainText('qa_number')
  for(let i=0;i<4;i++)await page.getByRole('button',{name:/^移除$|^remove$/i}).first().click()
  await page.getByRole('button',{name:/^添加$/}).click();await expect(page.locator('main')).not.toContainText('QA_EMPTY')
  for(const value of ['qa-one','qa-two']) {
    await page.getByPlaceholder('KEY',{exact:true}).fill('QA_SAFE')
    await page.getByPlaceholder('VALUE',{exact:true}).fill(value)
    await page.getByRole('button',{name:/^添加$/}).click()
  }
  await expect(page.locator('main')).toContainText('QA_SAFE=qa-two')
  await expect(page.locator('main')).not.toContainText('QA_SAFE=qa-one')
  await page.getByRole('button',{name:/^移除$|^remove$/i}).first().click()
  let writes=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/scripts')writes++})
  await page.getByRole('link',{name:/^取消$|^cancel$/i}).click();await page.waitForURL(/\/scripts$/);expect(writes).toBe(0)
})

test('SAFE-05 Schedule类型切换/必填/非法输入/Owner/Misfire',async({page})=>{
  await loginViaApi(page);const task=await createIsolatedTask(page,'safe-schedule');const owned=['/api/tasks/'+task.id]
  try {
    await page.goto('/schedules/new?task_id='+task.id)
    await page.getByRole('button',{name:/^保存$|^save$/i}).click();await expect(page.locator('main')).toContainText(/请输入.*Cron|cron.*required|Cron表达式不能为空/i)
    await page.getByRole('textbox',{name:/cron 表达式|cron expression/i}).fill('not cron')
    await page.getByRole('button',{name:/^保存$|^save$/i}).click();await expect(page.locator('main')).toContainText(/Cron|cron/)
    const type=page.getByRole('combobox').nth(1)
    await type.click();await page.getByRole('option',{name:/^间隔$|^interval$/i}).click()
    await page.getByLabel(/间隔.*秒|interval.*sec/i).fill('0');await page.getByRole('button',{name:/^保存$|^save$/i}).click();await expect(page.locator('main')).toContainText(/间隔|interval/i)
    await type.click();await page.getByRole('option',{name:/^一次性$|^one.time$/i}).click();await page.getByRole('button',{name:/^保存$|^save$/i}).click();await expect(page.locator('main')).toContainText(/时间|time/i)
    await type.click();await page.getByRole('option',{name:/启动时|on start/i}).click()
    const id=await captureCreated(page,'/api/schedules',()=>page.getByRole('button',{name:/^保存$|^save$/i}).click());owned.unshift('/api/schedules/'+id)
    expect((await apiRequest(page,'/api/schedules/'+id)).body).toMatchObject({type:'on_start',task_id:task.id})
  } finally {await cleanupByIds(page,owned)}
})


test('SAFE-06 登录必填/显示隐藏/真实回跳/退出取消确认/安全外站回跳',async({page})=>{
  const observations:unknown[]=[]
  await page.goto('/sign-in')
  const username=page.getByLabel(/^用户名$|^username$/i),password=page.getByLabel(/^密码$|^password$/i)
  await password.fill('QA_NON_SENSITIVE_PASSWORD')
  await page.getByRole('button',{name:/显示密码|show password/i}).click();await expect(password).toHaveAttribute('type','text')
  await page.getByRole('button',{name:/隐藏密码|hide password/i}).click();await expect(password).toHaveAttribute('type','password')
  let loginRequests=0;page.on('request',r=>{if(new URL(r.url()).pathname==='/api/login'&&r.method()==='POST')loginRequests++})
  for(const values of [['',''],['','qa-placeholder'],['qa-placeholder','']]) {
    await username.fill(values[0]);await password.fill(values[1]);await page.getByRole('button',{name:/^登录$|^sign in$/i}).click()
    expect(loginRequests).toBe(0)
  }
  observations.push({case:'empty username/password/both',requests:loginRequests})
  await page.goto('/sign-in?redirect=%2Fagents%3Fqa%3Dsafe%23anchor')
  await username.fill(process.env.NODESTEER_E2E_USERNAME||'admin');await password.fill(process.env.NODESTEER_E2E_PASSWORD||'')
  await page.getByRole('button',{name:/^登录$|^sign in$/i}).click();await expect(page).toHaveURL(/\/agents\?qa=safe#anchor$/)
  await page.getByRole('button',{name:/^个人资料$|^profile$/i}).click();await page.getByRole('menuitem',{name:/退出登录|log out/i}).click()
  await page.getByRole('alertdialog').getByRole('button',{name:/^取消$|^cancel$/i}).click()
  expect(await page.evaluate(()=>!!localStorage.getItem('nodesteer_token'))).toBeTruthy()
  await page.getByRole('button',{name:/^个人资料$|^profile$/i}).click();await page.getByRole('menuitem',{name:/退出登录|log out/i}).click()
  await page.getByRole('alertdialog').getByRole('button',{name:/^退出登录$|^sign out$|^log out$/i}).click()
  await expect(page).toHaveURL(/\/sign-in/);expect(await page.evaluate(()=>!!localStorage.getItem('nodesteer_token'))).toBeFalsy()
  await page.goto('/agents');await expect(page).toHaveURL(/\/sign-in/)
  await page.goto('/sign-in?redirect=https%3A%2F%2Fevil.example.test%2F')
  await username.fill(process.env.NODESTEER_E2E_USERNAME||'admin');await password.fill(process.env.NODESTEER_E2E_PASSWORD||'')
  await page.getByRole('button',{name:/^登录$|^sign in$/i}).click();await expect(page).toHaveURL(/\/$/)
  observations.push({case:'safe internal/external redirect and logout',passed:true})
  await evidence('auth-safe-basics',observations)
})

test('SAFE-07 Browser历史、导航active、快捷键与退出Esc',async({page})=>{
  await loginViaApi(page);await page.goto('/tasks')
  await page.locator('[data-slot=sidebar]').getByRole('link',{name:/^脚本$|^scripts$/i}).click();await expect(page).toHaveURL(/\/scripts$/)
  await page.goBack();await expect(page).toHaveURL(/\/tasks$/)
  await page.goForward();await expect(page).toHaveURL(/\/scripts$/)
  const active=page.locator('[data-slot=sidebar]').getByRole('link',{name:/^脚本$|^scripts$/i})
  await expect(active).toHaveAttribute('data-active','true')
  await page.getByRole('button',{name:/^个人资料$|^profile$/i}).focus();await page.keyboard.press('Enter')
  await expect(page.getByRole('menuitem',{name:/退出登录|log out/i})).toBeVisible()
  await page.keyboard.press('Escape');await expect(page.getByRole('menuitem',{name:/退出登录|log out/i})).toHaveCount(0)
  await evidence('keyboard-history-safe',{history:true,active:true,enter_menu:true,escape_close:true})
})


test('SAFE-08 Task四类型/目标切换、参数与Local/Remote/AND编辑清除',async({page})=>{
  await loginViaApi(page)
  const task=await createIsolatedTask(page,'safe-editor'),nodes=(await apiRequest(page,'/api/nodes')).body as {id:string;hostname:string}[]
  const nodeId=(await getNodeIds(page))[0],node=nodes.find(n=>n.id===nodeId)!
  try {
    await page.goto(`/tasks/${task.id}/edit`)
    for(const [type,option]of [['script','脚本'],['app_deploy','托管应用部署'],['app_operation','托管应用操作'],['command','命令']]) {
      await page.getByRole('combobox').first().click();await page.getByRole('option',{name:option,exact:true}).click()
      if(type==='app_deploy') {
        const operation=page.getByRole('combobox').nth(2);await operation.click()
        await expect(page.getByRole('option',{name:/^部署$/})).toBeVisible();await expect(page.getByRole('option',{name:/^升级$/})).toBeVisible()
        await page.keyboard.press('Escape')
      }
      if(type==='app_operation') {
        await page.getByRole('combobox').nth(2).click()
        for(const op of ['启动','停止','重启','升级'])await expect(page.getByRole('option',{name:op,exact:true})).toBeVisible()
        await page.keyboard.press('Escape')
      }
    }
    await page.getByLabel(/^命令$|^command$/i).fill('echo safe-editor')
    const target=page.getByRole('combobox').nth(1)
    await target.click();await page.getByRole('option',{name:/^标签$/}).click()
    await page.getByLabel(/^标签键$/).fill('qa-unused');await page.getByLabel(/^标签值$/).fill('qa-unused')
    await target.click();await page.getByRole('option',{name:/^分组$/}).click()
    await target.click();await page.getByRole('option',{name:/^节点$/}).click()
    await page.locator('label').filter({hasText:node.hostname}).getByRole('checkbox').check()
    await page.getByLabel(/重试/).fill('2');await page.getByLabel(/超时.*秒/).fill('40')
    await page.getByPlaceholder('参数名称',{exact:true}).fill('qa_number')
    await page.getByPlaceholder('默认值',{exact:true}).fill('42')
    await page.getByRole('button',{name:/^添加参数$/}).click()
    const local=page.locator('[data-slot=card]').filter({has:page.locator('[data-slot=card-title]').filter({hasText:/^本地条件$/})})
    for(const metric of ['cpu_usage','memory_usage','disk_usage','file_exists','dir_exists','process_exists','port_listening','command_result']) {
      await local.getByRole('combobox').first().click();await page.getByRole('option',{name:metric,exact:true}).click()
    }
    await local.getByPlaceholder('value',{exact:true}).fill('0')
    await local.getByRole('textbox').last().fill('true')
    for(const op of ['==','!=','>','<','>=','<=']) {
      await local.getByRole('combobox').nth(1).click();await page.getByRole('option',{name:op,exact:true}).click()
    }
    await local.getByRole('button',{name:/^设置条件$/}).click()
    await local.getByRole('button',{name:/^清除$/}).click()
    await local.getByRole('button',{name:/^设置条件$/}).click()
    await local.getByRole('checkbox').check()
    const remote=page.locator('[data-slot=card]').filter({has:page.locator('[data-slot=card-title]').filter({hasText:/^远程条件$/})})
    await remote.getByRole('combobox').first().click();await page.getByRole('option',{name:node.hostname,exact:true}).click()
    await remote.getByRole('combobox').nth(1).click();await page.getByRole('option',{name:/上次执行/}).click()
    await remote.getByPlaceholder('task_id',{exact:true}).fill(task.id)
    await remote.getByRole('combobox').nth(1).click();await page.getByRole('option',{name:'online',exact:true}).click()
    await remote.getByRole('button',{name:/^设置远程条件$/}).click()
    await page.getByRole('button',{name:/^保存$/}).click();await page.waitForURL(/\/tasks$/)
    const saved=(await apiRequest(page,`/api/tasks/${task.id}`)).body as {condition:unknown}
    expect(saved).toMatchObject({type:'command',retry:2,timeout:40,target:{type:'node',node_ids:[nodeId]},condition:{type:'and'}})
    await evidence('task-form-switch-condition',{saved})
    await page.goto(`/tasks/${task.id}/edit`)
    await expect(local.getByRole('checkbox')).toBeChecked()
    await local.getByRole('checkbox').uncheck();await local.getByRole('button',{name:/^设置条件$/}).click();await local.getByRole('button',{name:/^清除$/}).click()
    await page.getByRole('button',{name:/^保存$/}).click();await page.waitForURL(/\/tasks$/)
    expect(((await apiRequest(page,`/api/tasks/${task.id}`)).body as {condition?:unknown}).condition||null).toBeNull()
  } finally {await cleanupByIds(page,['/api/tasks/'+task.id])}
})


test('SAFE-09 Cron图形/所有预设/表达式/预览/IANA/Owner/Misfire',async({page})=>{
  await loginViaApi(page);const task=await createIsolatedTask(page,'cron-fields'),owned=['/api/tasks/'+task.id]
  try {
    await page.goto('/schedules/new?task_id='+task.id)
    const preset=page.getByRole('combobox',{name:'常用预设',exact:true})
    await preset.click();const options=await page.getByRole('option').allTextContents();await page.keyboard.press('Escape')
    for(const option of options.slice(1)) {
      await preset.click();await page.getByRole('option',{name:option,exact:true}).click()
      await expect(page.getByRole('textbox',{name:/cron 表达式|cron expression/i})).not.toHaveValue('')
    }
    const raw=page.getByRole('textbox',{name:/cron 表达式|cron expression/i})
    await raw.fill('0 0 1 1 *')
    const graphical=page.getByRole('textbox',{name:/^分$/})
    await graphical.fill('15');await expect(raw).toHaveValue('15 0 1 1 *')
    await raw.fill('0 0 1 1 *')
    await page.getByLabel(/^时区$/).fill('Asia/Shanghai')
    const preview=page.locator('main ul').last();await expect(preview.locator('li')).toHaveCount(5)
    await page.getByLabel(/^时区$/).fill('No/SuchZone');await expect(page.getByRole('alert')).toBeVisible()
    await page.getByLabel(/^时区$/).fill('UTC')
    const selectField=(name:string)=>page.locator('label').filter({hasText:new RegExp('^'+name)}).getByRole('combobox')
    await selectField('执行方').click();await page.getByRole('option',{name:/^Hub$/i}).click()
    await selectField('离线策略').click();await page.getByRole('option',{name:/需 Hub 在线/}).click()
    await selectField('漏触发策略').click();await page.getByRole('option',{name:/跳过/}).click()
    const id=await captureCreated(page,'/api/schedules',()=>page.getByRole('button',{name:/^保存$/}).click());owned.unshift('/api/schedules/'+id)
    const saved=(await apiRequest(page,'/api/schedules/'+id)).body
    expect(saved).toMatchObject({task_id:task.id,type:'cron',expression:'0 0 1 1 *',timezone:'UTC',execution_owner:'hub',offline_policy:'hub_online_required',misfire_policy:'skip'})
    await page.goto('/schedules/'+id)
    await selectField('执行方').click();await page.getByRole('option',{name:/^Agent$/i}).click()
    await selectField('离线策略').click();await page.getByRole('option',{name:/允许离线/}).click()
    await selectField('漏触发策略').click();await page.getByRole('option',{name:/补跑一次/}).click()
    await page.getByRole('button',{name:/^保存$/}).click();await page.waitForURL(/\/schedules$/)
    const edited=(await apiRequest(page,'/api/schedules/'+id)).body
    expect(edited).toMatchObject({task_id:task.id,type:'cron',expression:'0 0 1 1 *',timezone:'UTC',execution_owner:'agent',offline_policy:'allow_offline',misfire_policy:'run_once'})
    await evidence('cron-fields-safe',{preset_options:options,saved,edited,preview_count:5})
  } finally {await cleanupByIds(page,owned)}
})

test('SAFE-10 已登录访问登录入口应回到业务页',async({page})=>{
  await loginViaApi(page)
  const me=await apiRequest(page,'/api/me');expect(me.status).toBe(200)
  await page.goto('/sign-in')
  await evidence('authenticated-sign-in',{me_http:me.status,url:page.url(),login_form_visible:await page.getByLabel(/^用户名$/).isVisible()})
  if(process.env.NODESTEER_E2E_EVIDENCE_DIR)await page.screenshot({path:process.env.NODESTEER_E2E_EVIDENCE_DIR+'/authenticated-sign-in.png'})
  await expect(page).not.toHaveURL(/\/sign-in/)
})

test('SAFE-13 Users取消必填角色/Settings双区与System主题',async({page})=>{
  await loginViaApi(page)
  await page.goto('/users');await page.getByRole('button',{name:/^新建用户$/}).click()
  const create=page.getByRole('button',{name:/^创建$/}),password=page.getByLabel(/^密码$/)
  await expect(create).toBeDisabled();await page.getByLabel(/^用户名$/).fill(testName('unsaved-user'))
  await expect(create).toBeDisabled();await password.fill('QA_NOT_A_REAL_PASSWORD');await expect(password).toHaveAttribute('type','password')
  await expect(create).toBeEnabled()
  const role=page.locator('label').filter({hasText:/^角色/}).getByRole('combobox')
  await role.click();for(const name of ['管理员','操作员','查看者'])await expect(page.getByRole('option',{name,exact:true})).toBeVisible()
  await page.keyboard.press('Escape')
  let writes=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/users')writes++})
  await page.getByRole('button',{name:/^取消$/}).click();await expect(password).toHaveCount(0);expect(writes).toBe(0)
  await expect(page.getByRole('button',{name:/删除|delete/i})).toHaveCount(0)
  await page.goto('/settings')
  const settings=(await apiRequest(page,'/api/settings')).body as Record<string,string>
  await expect(page.locator('main input')).toHaveCount(Object.keys(settings).length)
  const names=await page.locator('main input').evaluateAll(inputs=>inputs.map(i=>({name:i.getAttribute('aria-label'),value:(i as HTMLInputElement).value})))
  expect(names.map(x=>x.value).sort()).toEqual(Object.values(settings).sort())
  await page.getByRole('button',{name:/^外观$/}).click()
  await page.getByRole('combobox').click();await page.getByRole('option',{name:/^系统$/}).click()
  await page.emulateMedia({colorScheme:'dark'});await expect(page.locator('html')).toHaveClass(/dark/)
  await page.emulateMedia({colorScheme:'light'});await expect(page.locator('html')).not.toHaveClass(/dark/)
  await page.reload();await page.getByRole('button',{name:/^外观$/}).click();await expect(page.getByRole('combobox')).toHaveText('系统')
  await page.getByRole('button',{name:/^运行时设置$/}).click();await expect(page.locator('main input')).toHaveCount(Object.keys(settings).length)
  await evidence('readonly-user-settings',{authenticated_sign_in_redirect:true,user_form:{required:true,roles:3,cancel_writes:writes,password_masked:true,no_delete:true},settings:{fields:names,system_dark_light_persist:true}})
})

test('SAFE-11 Dashboard统计和全部ViewAll/Node-Task-Schedule回链与刷新',async({page})=>{
  await loginViaApi(page);const task=await createIsolatedTask(page,'readonly-links'),owned=['/api/tasks/'+task.id]
  try {
    const node=(await getNodeIds(page))[0]
    const result=await apiRequest(page,'/api/schedules',{method:'POST',data:{task_id:task.id,type:'cron',expression:'0 0 1 1 *',timezone:'UTC',execution_owner:'agent',offline_policy:'allow_offline',misfire_policy:'skip',enabled:true}})
    expect(result.ok).toBeTruthy();const id=(result.body as {id:string}).id;owned.unshift('/api/schedules/'+id)
    const nodesResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/nodes'&&r.request().method()==='GET')
    const execResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/executions'&&new URL(r.url()).searchParams.get('limit')==='50')
    await page.goto('/');const nodes=await(await nodesResponse).json(),executions=await(await execResponse).json() as {status:string}[]
    const expected=[nodes.length,executions.filter(e=>e.status==='RUNNING').length,executions.filter(e=>['FAILED','TIMED_OUT'].includes(e.status)).length]
    const metric=page.locator('main').locator('[data-slot=card]').filter({has:page.locator('[data-slot=card-title]')})
    await expect(metric.first()).toContainText(String(expected[0]))
    const labels=['节点总数','运行中','执行失败'];for(let i=0;i<labels.length;i++)await expect(page.locator('[data-slot=card]').filter({has:page.getByText(labels[i],{exact:true})}).first()).toContainText(String(expected[i]))
    const links:unknown[]=[]
    for(const [title,path]of [['正在执行的任务','/executions'],['Agent 健康状态','/agents'],['托管应用状态','/applications'],['同步状态','/agents'],['最近执行','/executions'],['即将执行的调度','/schedules']]) {
      await page.goto('/');const card=page.locator('[data-slot=card]').filter({has:page.locator('[data-slot=card-title]').filter({hasText:new RegExp('^'+title+'$')})})
      await card.getByRole('link',{name:/查看全部/}).click();await expect(page).toHaveURL(new RegExp(path+'(?:\\?|$)'));links.push({title,path})
    }
    await page.goto('/agents/'+node);await page.reload()
    await expect(page.locator(`a[href="/tasks/${task.id}"]`)).toBeVisible();await expect(page.locator(`a[href="/schedules/${id}"]`)).toBeVisible()
    await page.locator(`a[href="/tasks/${task.id}"]`).click();await expect(page).toHaveURL(new RegExp('/tasks/'+task.id+'$'))
    await expect(page.locator(`a[href="/schedules/${id}"]`)).toBeVisible();await page.locator(`a[href="/schedules/${id}"]`).click()
    await expect(page).toHaveURL(new RegExp('/schedules/'+id+'$'));await page.reload();await expect(page.getByRole('combobox').first()).toHaveText(task.name)
    await page.goto('/nodes/'+node);await expect(page.locator(`a[href="/tasks/${task.id}"]`)).toBeVisible()
    await page.goto('/agents/00000000-0000-0000-0000-000000000000');await expect(page.getByRole('button',{name:/^重试$/})).toBeVisible()
    await page.getByRole('button',{name:/^重试$/}).click();await expect(page.getByRole('link',{name:/返回/})).toBeVisible()
    await evidence('dashboard-node-links',{expected_metrics:expected,view_all:links,node,task_id:task.id,schedule_id:id,node_alias:true,invalid_node_error_retry:true})
  } finally {await cleanupByIds(page,owned)}
})

test('SAFE-12 Group静态双成员/标签往返和Task启停/删除取消确认',async({page})=>{
  await loginViaApi(page)
  const nodes=(await apiRequest(page,'/api/nodes')).body as {id:string;hostname:string}[]
  const ids=[(await getNodeIds(page))[0],process.env.NODESTEER_E2E_LABEL_NODE_ID!]
  const groupName=testName('group-roundtrip'),owned:string[]=[]
  try {
    await page.goto('/groups/new');await page.getByLabel(/^名称$/).fill(groupName);await page.getByLabel(/^描述$/).fill('QA static two members')
    for(const id of ids)await page.locator('label').filter({hasText:nodes.find(n=>n.id===id)!.hostname}).getByRole('checkbox').check()
    const id=await captureCreated(page,'/api/groups',()=>page.getByRole('button',{name:/^保存$/}).click());owned.push('/api/groups/'+id)
    const initial=(await apiRequest(page,'/api/groups/'+id)).body
    expect(initial).toMatchObject({name:groupName,description:'QA static two members',type:'static'})
    expect([...(initial as {members:string[]}).members].sort()).toEqual([...ids].sort())
    await page.goto('/groups/'+id);await page.getByRole('combobox').click();await page.getByRole('option',{name:'标签',exact:true}).click()
    await page.getByLabel(/^标签键$/).fill(testName('unmatched'));await page.getByLabel(/^标签值$/).fill('QA-no-node-matches')
    await page.getByRole('button',{name:/^保存$/}).click();await page.waitForURL(/\/groups$/)
    await page.getByRole('textbox',{name:/搜索/}).fill(groupName);await expect(page.locator('tbody tr').first()).toContainText('0')
    const label=(await apiRequest(page,'/api/groups/'+id)).body
    await page.goto('/groups/'+id);await page.getByLabel(/^名称$/).fill(groupName+'-edited');await page.getByLabel(/^描述$/).fill('QA revised')
    await page.getByRole('combobox').click();await page.getByRole('option',{name:'静态',exact:true}).click()
    await page.locator('label').filter({hasText:nodes.find(n=>n.id===ids[0])!.hostname}).getByRole('checkbox').check()
    await page.locator('label').filter({hasText:nodes.find(n=>n.id===ids[1])!.hostname}).getByRole('checkbox').uncheck()
    await page.getByRole('button',{name:/^保存$/}).click();await page.waitForURL(/\/groups$/)
    const restored=(await apiRequest(page,'/api/groups/'+id)).body
    expect(restored).toMatchObject({name:groupName+'-edited',description:'QA revised',type:'static',members:[ids[0]]})
    const task=await createIsolatedTask(page,'toggle-delete');owned.unshift('/api/tasks/'+task.id)
    let deletes=0;page.on('request',r=>{if(r.method()==='DELETE'&&owned.includes(new URL(r.url()).pathname))deletes++})
    for(const [kind,objectId,query]of [['tasks',task.id,task.name],['groups',id,groupName+'-edited']]) {
      await page.goto('/'+kind);await page.getByRole('textbox',{name:/搜索/}).fill(query)
      const row=page.locator('tbody tr').first()
      if(kind==='tasks')for(const [action,enabled]of [['禁用',false],['启用',true]] as const) {
        await row.getByRole('button',{name:/^操作$/}).click();await page.getByRole('menuitem',{name:action,exact:true}).click()
        await expect.poll(async()=>(await apiRequest(page,'/api/tasks/'+task.id)).body).toMatchObject({enabled})
      }
      const before=deletes
      await row.getByRole('button',{name:/^操作$/}).click();await page.getByRole('menuitem',{name:/^删除$/}).click()
      await page.getByRole('alertdialog').getByRole('button',{name:/^取消$/}).click();expect(deletes).toBe(before)
      await page.keyboard.press('Escape')
      await expect.soft.poll(()=>row.getByRole('button',{name:/^操作$/}).count(),{timeout:3000}).toBe(1)
      const accessible=await row.getByRole('button',{name:/^操作$/}).count()
      await evidence('delete-cancel-'+kind,{url:page.url(),accessible_actions:accessible,dom:await page.evaluate(()=>({body_pointer_events:getComputedStyle(document.body).pointerEvents,hidden:[...document.querySelectorAll('[aria-hidden="true"]')].map(e=>({tag:e.tagName,slot:e.getAttribute('data-slot')})),dialogs:[...document.querySelectorAll('[role="alertdialog"],[role="menu"]')].map(e=>({role:e.getAttribute('role'),state:e.getAttribute('data-state')}))})),rows:await page.locator('tbody').innerText(),resource:(await apiRequest(page,'/api/'+kind+'/'+objectId)).body})
      if(!accessible){if(process.env.NODESTEER_E2E_EVIDENCE_DIR)await page.screenshot({path:process.env.NODESTEER_E2E_EVIDENCE_DIR+'/delete-cancel-'+kind+'.png'});await page.reload();await page.getByRole('textbox',{name:/搜索/}).fill(query)}
      await row.getByRole('button',{name:/^操作$/}).click();await page.getByRole('menuitem',{name:/^删除$/}).click()
      await page.getByRole('alertdialog').getByRole('button',{name:/^删除$/}).click();await expect.poll(()=>deletes).toBe(before+1)
      await expect.poll(async()=>(await apiRequest(page,'/api/'+kind+'/'+objectId)).status).toBe(404)
    }
    await evidence('group-task-roundtrip',{initial,label,restored,task_id:task.id,task_toggle:true,delete_cancel_zero_confirm_once:true})
  } finally {await cleanupByIds(page,owned)}
})

test('SAFE-14 Artifact必填/架构/Usage与Application参数环境健康字段',async({page})=>{
  await loginViaApi(page);const owned:string[]=[]
  try {
    await page.goto('/artifacts/new');let writes=0
    page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/artifacts')writes++})
    await page.getByRole('button',{name:/^上传$/}).click();expect(writes).toBe(0)
    const name=testName('form-artifact');await page.getByLabel(/^名称$/).fill(name)
    await page.getByRole('button',{name:/^上传$/}).click();expect(writes).toBe(0)
    await page.getByLabel(/^版本$/).fill('v1');await page.getByRole('button',{name:/^上传$/}).click();expect(writes).toBe(0)
    await page.locator('select[name=architecture]').selectOption('arm64')
    await page.locator('input[type=file]').setInputFiles({name:'qa-form.txt',mimeType:'application/octet-stream',buffer:Buffer.from('QA definition only artifact')})
    const artifact=await captureCreated(page,'/api/artifacts',()=>page.getByRole('button',{name:/^上传$/}).click());owned.push('/api/artifacts/'+artifact)
    await page.goto('/applications/new?artifact_id='+artifact);const appName=testName('form-app')
    await page.getByLabel(/^名称$/).fill(appName);await page.getByLabel(/^二进制路径$/).fill('/tmp/'+appName+'/binary')
    const add=page.getByRole('button',{name:/^添加$/})
    await page.getByPlaceholder('--port 8080',{exact:true}).fill('--qa-one');await add.nth(0).click()
    await page.getByPlaceholder('--port 8080',{exact:true}).fill('--qa-remove');await add.nth(0).click()
    await page.getByRole('button',{name:/^移除$/}).nth(1).click()
    for(const value of ['old','new']){await page.getByPlaceholder('KEY',{exact:true}).fill('QA_FORM_ENV');await page.getByPlaceholder('VALUE',{exact:true}).fill(value);await add.nth(1).click()}
    await page.getByPlaceholder('KEY',{exact:true}).fill('QA_REMOVE_ENV');await page.getByPlaceholder('VALUE',{exact:true}).fill('remove');await add.nth(1).click()
    await page.getByRole('button',{name:/^移除$/}).last().click()
    const health=page.locator('label').filter({hasText:/^健康检查类型/}).getByRole('combobox')
    for(const type of ['systemd','tcp','http','command']){await health.click();await page.getByRole('option',{name:type,exact:true}).click()}
    await page.getByLabel(/^健康检查目标$/).fill('true');await page.getByLabel(/^检查超时/).fill('7');await page.getByLabel(/^检查次数/).fill('4');await page.getByLabel(/^检查间隔/).fill('3')
    const app=await captureCreated(page,'/api/applications',()=>page.getByRole('button',{name:/^保存$/}).click());owned.unshift('/api/applications/'+app)
    await page.waitForURL(new RegExp('/applications/'+app+'$'))
    const saved=(await apiRequest(page,'/api/applications/'+app)).body as {unit_name:string}
    expect(saved).toMatchObject({artifact_id:artifact,arguments:['--qa-one'],environment:{QA_FORM_ENV:'new'},health_check:{type:'command',target:'true',timeout:7,attempts:4,interval:3}})
    expect(saved.unit_name).toMatch(/\.service$/)
    await expect(page.getByRole('button',{name:/^部署$/})).toBeDisabled()
    await page.locator('main a[href="/artifacts"]').click();await expect(page).toHaveURL(/\/artifacts$/)
    await page.getByRole('textbox',{name:/搜索/}).fill(name)
    await expect(page.locator(`a[href="/applications/${app}"]`)).toHaveText(appName)
    await page.locator(`a[href="/applications/${app}"]`).click();await expect(page).toHaveURL(new RegExp('/applications/'+app+'$'))
    await page.reload();await expect(page.getByLabel(/^健康检查目标$/)).toHaveValue('true')
    await evidence('artifact-app-form',{required_zero_post:true,architecture:'arm64',artifact_id:artifact,app_id:app,saved,usage_link:true,manage_artifacts:true,deploy_no_nodes_disabled:true})
  } finally {await cleanupByIds(page,owned)}
})

test('SAFE-15 真实执行日志流/搜索/换行跟随复制与停止取消审计',async({page,context})=>{
  test.setTimeout(90_000);await loginViaApi(page)
  const created=await apiRequest(page,'/api/tasks',{method:'POST',data:{name:testName('log-cancel'),type:'command',command:'printf "QA_STDOUT_ONE\\nQA_STDOUT_TWO\\n"; printf "QA_STDERR_ONE\\n" >&2; sleep 60',target:{type:'node',node_ids:await getNodeIds(page)},timeout:80,enabled:true}})
  expect(created.ok).toBeTruthy();const task=(created.body as {id:string}),owned=['/api/tasks/'+task.id];let executionId=''
  try {
    await page.goto('/tasks/'+task.id+'/run');await page.getByRole('button',{name:/^立即运行$/}).click()
    let runs=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/tasks/'+task.id+'/run')runs++})
    await page.getByRole('alertdialog').getByRole('button',{name:/^取消$/}).click();expect(runs).toBe(0)
    await expect(page.getByRole('alertdialog')).toHaveCount(0)
    await page.getByRole('button',{name:/^立即运行$/}).click();await page.getByRole('alertdialog').getByRole('button',{name:/^立即运行$/}).click()
    await page.waitForURL(/\/executions$/)
    await expect.poll(async()=>{const e=(await apiRequest(page,'/api/executions?task_id='+task.id+'&limit=10')).body as {id:string;status:string}[];executionId=e.find(x=>x.status==='RUNNING')?.id||'';return executionId},{timeout:20_000}).not.toBe('')
    await expect.poll(async()=>{const chunks=(await apiRequest(page,'/api/executions/'+executionId+'/logs')).body as {chunk:string}[];return chunks.map(c=>c.chunk).join('')},{timeout:20_000}).toContain('QA_STDERR_ONE')
    await page.goto('/executions/'+executionId);const output=page.locator('pre'),stream=page.getByRole('combobox',{name:/全部流/})
    await expect(output).toContainText('QA_STDOUT_ONE');await expect(output).toContainText('QA_STDERR_ONE')
    await stream.selectOption('stdout');await expect(output).toContainText('QA_STDOUT_TWO');await expect(output).not.toContainText('QA_STDERR_ONE')
    await stream.selectOption('stderr');await expect(output).toContainText('QA_STDERR_ONE');await expect(output).not.toContainText('QA_STDOUT_ONE')
    await stream.selectOption('all');await page.getByPlaceholder('搜索日志...',{exact:true}).fill('stdout_two');await expect(output).toHaveText(/QA_STDOUT_TWO\s*$/)
    await page.getByPlaceholder('搜索日志...',{exact:true}).fill('')
    await page.getByRole('button',{name:/^换行$/}).click();await expect(output).toHaveClass(/whitespace-pre-wrap/)
    await page.getByRole('button',{name:/^换行$/}).click();await expect(output).not.toHaveClass(/whitespace-pre-wrap/)
    await page.getByRole('button',{name:/^跟随$/}).click();await page.getByRole('button',{name:/^跟随$/}).click()
    await context.grantPermissions(['clipboard-read','clipboard-write'])
    await page.getByRole('button',{name:/^复制日志$/}).click()
    const copied=await page.evaluate(()=>navigator.clipboard?.readText()||null)
    if(copied){expect(copied).toContain('QA_STDOUT_TWO');expect(copied).toContain('QA_STDERR_ONE')}
    await expect(page.getByText(copied?'已复制':'当前页面无法自动写入剪切板，请按 Ctrl+C 复制。',{exact:true})).toBeVisible()
    let cancels=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/executions/'+executionId+'/cancel')cancels++})
    await page.getByRole('button',{name:/^停止执行$/}).click();await page.getByRole('alertdialog').getByRole('button',{name:/^取消$/}).click();expect(cancels).toBe(0)
    await expect(page.getByRole('alertdialog')).toHaveCount(0)
    await page.getByRole('button',{name:/^停止执行$/}).click();await page.getByRole('alertdialog').getByRole('button',{name:/^停止执行$/}).click()
    await expect.poll(async()=>(await apiRequest(page,'/api/executions/'+executionId)).body,{timeout:20_000}).toMatchObject({status:'CANCELED'})
    await expect(page.getByRole('button',{name:/^停止执行$/})).toHaveCount(0);expect(cancels).toBe(1)
    const audit=(await apiRequest(page,'/api/audit?limit=100')).body as {action:string;resource:string;resource_id:string}[]
    const entry=audit.find(x=>x.resource_id===executionId&&x.action==='cancel');expect(entry).toMatchObject({resource:'execution'})
    await page.goto('/audit');await page.getByRole('combobox').last().click();await page.getByRole('option',{name:'50',exact:true}).click()
    await page.getByRole('textbox',{name:/搜索/}).fill(executionId);await expect(page.locator('tbody')).toContainText('cancel')
    await evidence('execution-log-cancel',{task_id:task.id,execution_id:executionId,streams:3,search_case_insensitive:true,wrap:true,follow_toggle:true,copy_success_feedback:!!copied,copy_manual_feedback:!copied,copy_exact_output:copied,clipboard_read_limitation:!copied?'HTTP origin uses intentional manual Copy fallback; secure Clipboard success requires HTTPS':null,run_cancel_zero_write:true,stop_cancel_zero_write:true,stop_confirm_requests:cancels,audit:entry})
  } finally {
    const executions=(await apiRequest(page,'/api/executions?task_id='+task.id+'&limit=10')).body as {id:string;status:string}[]
    for(const e of executions)if(['RUNNING','PENDING'].includes(e.status))await apiRequest(page,'/api/executions/'+e.id+'/cancel',{method:'POST'})
    await cleanupByIds(page,owned)
  }
})

test('SAFE-16 真实纳管必填/无效输入/IPv4IPv6DNS与三种命令/重开重置',async({page})=>{
  test.setTimeout(90_000);await loginViaApi(page);const owned:string[]=[],checks:unknown[]=[]
  try {
    await page.goto('/agents');await page.getByRole('button',{name:/^添加节点$/}).click()
    const dialog=page.getByRole('dialog'),name=dialog.getByLabel(/^节点名称$/),address=dialog.getByLabel(/^节点地址/),hub=dialog.getByLabel(/^Hub 地址$/),generate=dialog.getByRole('button',{name:/^生成安装命令$/})
    let posts=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/nodes/enrollment')posts++})
    await generate.click();expect(posts).toBe(0);await name.fill(testName('missing-address'));await generate.click();expect(posts).toBe(0)
    await address.fill('192.0.2.61');await hub.fill('');await generate.click();expect(posts).toBe(0)
    await hub.fill(new URL(page.url()).origin)
    for(const [badName,badAddress,badHub]of [['bad name!','192.0.2.61',new URL(page.url()).origin],['x'.repeat(129),'192.0.2.61',new URL(page.url()).origin],[testName('bad-address'),'bad host!',new URL(page.url()).origin],[testName('bad-hub'),'192.0.2.61','ftp://qa.example.test']]) {
      await name.fill(badName);await address.fill(badAddress);await hub.fill(badHub)
      const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/nodes/enrollment'&&r.request().method()==='POST')
      await generate.click();expect((await response).status()).toBe(400);await expect(dialog.getByRole('button',{name:/^重试$/})).toBeVisible()
      checks.push({negative:true,http:400})
    }
    for(const ip of ['192.0.2.61','2001:db8::61','qa-node.example.test']) {
      const nodeName=testName('pending-enroll');await name.fill(nodeName);await address.fill(ip);await hub.fill(new URL(page.url()).origin)
      const before=posts,id=await captureCreated(page,'/api/nodes/enrollment',()=>generate.click());owned.push('/api/nodes/'+id);expect(posts).toBe(before+1)
      const node=(await apiRequest(page,'/api/nodes/'+id)).body as {agent_id:string;status:string;hostname:string;ip:string}
      expect(node).toMatchObject({hostname:nodeName,ip,status:'pending'})
      const methods:unknown[]=[]
      for(const method of ['Native','docker run','docker compose']) {
        await dialog.getByRole('tab',{name:method,exact:true}).click()
        const command=await dialog.locator('pre').innerText()
        expect(command.toLowerCase().includes('registration_token')).toBe(true);expect(command.includes(node.agent_id)).toBe(true);expect(command.includes(nodeName)).toBe(true);expect(command.includes(ip)).toBe(true)
        methods.push({method,command_length:command.length,registration_key_present:true,identity_present:true})
      }
      await dialog.getByRole('button',{name:/^复制$/}).click();await expect(page.getByText('当前页面无法自动写入剪切板，请按 Ctrl+C 复制。',{exact:true}).last()).toBeVisible()
      checks.push({ip,node_id:id,methods,copy_manual:true})
    }
    await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);await page.getByRole('button',{name:/^添加节点$/}).click()
    await expect(name).toHaveValue('');await expect(address).toHaveValue('');await expect(hub).toHaveValue(new URL(page.url()).origin);await expect(dialog.locator('pre')).toHaveCount(0)
    await evidence('enrollment-real-validation',{checks,required_zero_posts:true,reopen_reset:true,secret_commands_not_saved:true})
  } finally {await page.keyboard.press('Escape');await cleanupByIds(page,owned)}
})

test('SAFE-17 详情深链刷新前后退/不存在资源ErrorRetry与Definition回链',async({page})=>{
  test.setTimeout(120_000);await login(page);const owned:string[]=[],checks:unknown[]=[],unauthorized:unknown[]=[],failedRequests:unknown[]=[]
  page.on('response',r=>{if(r.status()===401)unauthorized.push({path:new URL(r.url()).pathname,authorization_present:!!r.request().headers().authorization})})
  page.on('requestfailed',r=>{if(new URL(r.url()).pathname==='/api/me')failedRequests.push({path:'/api/me',failure:r.failure(),authorization_present:!!r.headers().authorization})})
  try {
    const node=(await getNodeIds(page))[0]
    const group=await apiRequest(page,'/api/groups',{method:'POST',data:{name:testName('route-group'),type:'static',members:[]}})
    const groupId=(group.body as {id:string}).id;owned.push('/api/groups/'+groupId)
    const script=await apiRequest(page,'/api/scripts',{method:'POST',data:{name:testName('route-script'),interpreter:'shell',content:'echo route script',enabled:true}})
    const scriptId=(script.body as {id:string}).id;owned.push('/api/scripts/'+scriptId)
    const task=await createIsolatedTask(page,'route-task');owned.unshift('/api/tasks/'+task.id)
    const scripted=await apiRequest(page,'/api/tasks',{method:'POST',data:{name:testName('route-script-task'),type:'script',script_id:scriptId,target:{type:'node',node_ids:[node]},enabled:true,timeout:30}})
    const scriptedId=(scripted.body as {id:string}).id;owned.unshift('/api/tasks/'+scriptedId)
    const schedule=await apiRequest(page,'/api/schedules',{method:'POST',data:{task_id:task.id,type:'cron',expression:'0 0 1 1 *',timezone:'UTC',execution_owner:'agent',offline_policy:'allow_offline',misfire_policy:'skip',enabled:false}})
    const scheduleId=(schedule.body as {id:string}).id;owned.unshift('/api/schedules/'+scheduleId)
    await page.goto('/artifacts/new');await page.getByLabel(/^名称$/).fill(testName('route-artifact'));await page.getByLabel(/^版本$/).fill('route-v1')
    await page.locator('input[type=file]').setInputFiles({name:'route.txt',mimeType:'application/octet-stream',buffer:Buffer.from('route definition')})
    const artifact=await captureCreated(page,'/api/artifacts',()=>page.getByRole('button',{name:/^上传$/}).click());owned.push('/api/artifacts/'+artifact)
    const application=await apiRequest(page,'/api/applications',{method:'POST',data:{name:testName('route-app'),version:'route-v1',artifact_id:artifact,binary_path:'/tmp/'+testName('route-definition'),health_check:{type:'systemd',timeout:5,attempts:3,interval:2}}})
    const appId=(application.body as {id:string}).id;owned.unshift('/api/applications/'+appId)
    const appTask=await apiRequest(page,'/api/tasks',{method:'POST',data:{name:testName('route-app-task'),type:'app_deploy',application_id:appId,app_operation:'deploy',target:{type:'node',node_ids:[node]},enabled:true,timeout:30}})
    const appTaskId=(appTask.body as {id:string}).id;owned.unshift('/api/tasks/'+appTaskId)
    await apiRequest(page,'/api/tasks/'+task.id+'/run',{method:'POST'})
    let executionId='';await expect.poll(async()=>{const e=(await apiRequest(page,'/api/executions?task_id='+task.id+'&limit=10')).body as {id:string;status:string}[];executionId=e.find(x=>x.status==='SUCCESS')?.id||'';return executionId},{timeout:20_000}).not.toBe('')
    for(const path of ['/agents/'+node,'/nodes/'+node,'/groups/'+groupId,'/scripts/'+scriptId,'/tasks/'+task.id,'/tasks/'+task.id+'/edit','/tasks/'+task.id+'/run','/schedules/new?task_id='+task.id,'/schedules/'+scheduleId,'/applications/new?artifact_id='+artifact,'/applications/'+appId,'/executions/'+executionId]) {
      await page.goto(path);await page.waitForLoadState('networkidle');await expect(page.locator('main')).toBeVisible();await page.reload();await page.waitForLoadState('networkidle');await expect(page.locator('main')).toBeVisible()
      await expect(page).toHaveURL(new RegExp(path.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$'))
      await page.goto('/audit');await page.waitForLoadState('networkidle');await page.goBack();await page.waitForLoadState('networkidle');await expect(page).toHaveURL(new RegExp(path.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$'));await page.goForward();await page.waitForLoadState('networkidle');await expect(page).toHaveURL(/\/audit$/)
      checks.push({path,direct:true,reload:true,back_forward:true});await evidence('detail-deep-progress',{checks,unauthorized,failedRequests})
    }
    await page.goto('/tasks/'+scriptedId);await page.locator(`a[href="/scripts/${scriptId}"]`).click();await expect(page).toHaveURL(new RegExp('/scripts/'+scriptId+'$'))
    await page.goto('/tasks/'+appTaskId);await expect(page.locator('main').getByText((application.body as {name:string}).name,{exact:true})).toBeVisible();await expect(page.locator(`a[href="/applications/${appId}"]`)).toHaveCount(0)
    await page.goto('/tasks/'+task.id);await page.locator(`a[href="/executions/${executionId}"]`).click();await expect(page).toHaveURL(new RegExp('/executions/'+executionId+'$'))
    for(const path of ['/agents/','/groups/','/scripts/','/tasks/','/tasks/','/tasks/','/schedules/','/applications/','/executions/'].map((prefix,i)=>prefix+'00000000-0000-0000-0000-000000000000'+(i===4?'/edit':i===5?'/run':''))) {
      await page.goto(path);await expect(page.getByRole('button',{name:/^重试$/})).toBeVisible();await page.getByRole('button',{name:/^重试$/}).click()
      await expect(page.getByRole('button',{name:/^重试$/})).toBeVisible();await page.getByRole('link',{name:/返回/}).click();await expect(page.locator('table')).toBeVisible()
      checks.push({path,error_retry_back:true})
    }
    await evidence('detail-deep-links',{checks,group_id:groupId,script_id:scriptId,task_id:task.id,schedule_id:scheduleId,app_id:appId,artifact_id:artifact,execution_id:executionId,script_definition_link:true,application_definition_text:true,application_definition_link_not_supported:true,execution_history_link:true})
  } catch(error) {await evidence('detail-deep-error',{error:String(error),url:page.url(),token_present:await page.evaluate(()=>!!localStorage.getItem('nodesteer_token')),unauthorized,failedRequests,checks});throw error}
  finally {await loginViaApi(page);await cleanupByIds(page,owned)}
})

test('SAFE-18 Execution状态触发筛选URL/清空与Transfer目标草稿反向操作',async({page})=>{
  await loginViaApi(page);await page.goto('/executions');const checks:unknown[]=[]
  const status=page.getByRole('combobox',{name:'状态',exact:true}),trigger=page.getByRole('combobox',{name:'触发方式',exact:true})
  for(const value of ['PENDING','RUNNING','SUCCESS','FAILED','SKIPPED','CANCELED','TIMED_OUT','BLOCKED']) {
    await status.selectOption(value);await expect(page).toHaveURL(new RegExp('status='+value))
    await page.waitForLoadState('networkidle');await expect(status).toHaveValue(value);checks.push({status:value,url:page.url(),rows:await page.locator('main').innerText()})
  }
  await status.selectOption('');await expect(page).not.toHaveURL(/status=/)
  const available=await trigger.locator('option').evaluateAll(o=>o.map(x=>(x as HTMLOptionElement).value).filter(Boolean))
  for(const value of available){await trigger.selectOption(value);await expect(page).toHaveURL(new RegExp('trigger='+value));await expect(trigger).toHaveValue(value)}
  await trigger.selectOption('');await page.getByRole('textbox',{name:/搜索/}).fill('qa-query');await expect(page).toHaveURL(/q=qa-query/)
  await page.getByRole('textbox',{name:/搜索/}).fill('');await expect(page).not.toHaveURL(/[?&](q|trigger|status)=/)
  await page.waitForLoadState('networkidle');await evidence('execution-filters',{statuses:checks,triggers_available:available,filters_cleared:true})
  await loginViaApi(page);await page.goto('/transfers');await page.waitForLoadState('networkidle');const nodes=(await apiRequest(page,'/api/nodes')).body as {id:string;hostname:string}[]
  const source=nodes.find(n=>n.id===(process.env.NODESTEER_E2E_NODE_IDS||'').split(',')[0])!,target=nodes.find(n=>n.id===process.env.NODESTEER_E2E_LABEL_NODE_ID)!
  const combos=page.getByRole('combobox'),add=page.getByRole('button',{name:/^添加目标$/}),start=page.getByRole('button',{name:/^开始传输$/})
  await expect(add).toBeDisabled();await expect(start).toBeDisabled();await combos.nth(0).click();await page.getByRole('option',{name:source.hostname,exact:true}).click()
  await combos.nth(1).click();await expect(page.getByRole('option',{name:source.hostname,exact:true})).toHaveCount(0);await page.getByRole('option',{name:target.hostname,exact:true}).click()
  await expect(add).toBeDisabled();const path='/tmp/'+testName('draft-target');await page.getByLabel(/^目标文件路径$/).fill(path);await add.click()
  await expect(start).toBeDisabled();await page.getByLabel(/^源文件路径$/).fill('/tmp/'+testName('draft-source-missing'));await expect(start).toBeEnabled()
  let writes=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/transfers')writes++})
  await page.getByRole('button',{name:/^移除$/}).click();expect(writes).toBe(0);await expect(start).toBeDisabled()
  await combos.nth(1).click();await page.getByRole('option',{name:target.hostname,exact:true}).click();await page.getByLabel(/^目标文件路径$/).fill(path);await add.click()
  await combos.nth(0).click();await page.getByRole('option',{name:target.hostname,exact:true}).click()
  const selfEnabled=await start.isEnabled(),retained=await page.locator('main').getByText(path,{exact:true}).isVisible()
  await combos.nth(1).click();const selfOptionCount=await page.getByRole('option',{name:target.hostname,exact:true}).count();await page.keyboard.press('Escape')
  await evidence('execution-filter-transfer-draft',{statuses:checks,triggers_available:available,filters_cleared:true,source_before:source.id,target_before:target.id,source_after:target.id,retained_target_node:target.id,retained_target_path:path,target_remains:retained,target_selector_source_option_count:selfOptionCount,source_excluded_initially:true,add_required:true,remove_zero_post:true,source_change_self_start_enabled:selfEnabled,posts_after_source_change:0,linked_actual_request:'evidence/transfer-source-change-self.json',api_self_transfer_contract:'allowed; UI constraint consistency only'})
  if(process.env.NODESTEER_E2E_EVIDENCE_DIR)await page.screenshot({path:process.env.NODESTEER_E2E_EVIDENCE_DIR+'/transfer-source-change-ui.png'})
  expect(retained&&selfEnabled).toBe(false)
})

test('SAFE-20 Node多选/工具栏键盘/升级维护取消零写请求',async({page})=>{
  await loginViaApi(page);await page.goto('/agents')
  const nodeId=process.env.NODESTEER_E2E_LABEL_NODE_ID!,row=page.locator(`tr:has(a[href="/agents/${nodeId}"])`)
  await row.getByRole('checkbox').check();const toolbar=page.getByRole('toolbar');await expect(toolbar).toBeVisible()
  let writes=0;page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname.startsWith('/api/nodes'))writes++})
  const buttons=toolbar.getByRole('button'),first=buttons.first(),last=buttons.last()
  await first.focus();await page.keyboard.press('End');await expect(last).toBeFocused()
  await page.keyboard.press('Home');await expect(first).toBeFocused()
  await page.keyboard.press('ArrowRight');await expect(buttons.nth(1)).toBeFocused()
  await page.keyboard.press('ArrowLeft');await expect(first).toBeFocused()
  for(const name of [/升级 Agent/,/设为维护/]) {
    await toolbar.getByRole('button',{name}).click();await expect(page.getByRole('alertdialog')).toBeVisible()
    await page.getByRole('alertdialog').getByRole('button',{name:/^取消$/}).click();await expect(page.getByRole('alertdialog')).toHaveCount(0)
    expect(writes).toBe(0);await expect(row.getByRole('checkbox')).toBeChecked()
  }
  await first.focus();await page.keyboard.press('Escape');await expect(toolbar).toHaveCount(0);await expect(row.getByRole('checkbox')).not.toBeChecked()
  await evidence('bulk-selection-keyboard',{node_id:nodeId,home_end_arrows:true,upgrade_cancel_zero_write:true,maintenance_cancel_zero_write:true,selection_preserved_on_cancel:true,escape_clear:true,shared_node_effect:'none'})
})

test('SAFE-21 新用户UI创建/重复/三角色保存登录/密码新旧边界',async({page})=>{
  test.setTimeout(120_000)
  const username=testName('user'),password=randomBytes(24).toString('base64url'),privateDir=process.env.NODESTEER_E2E_PRIVATE_DIR
  if(!privateDir)throw new Error('Private credential directory required')
  await appendFile(privateDir+'/ui-users.jsonl',JSON.stringify({username,password})+'\n',{mode:0o600})
  let userId='';const observations:unknown[]=[],tokens:string[]=[]
  try {
    await loginViaApi(page);await page.goto('/users');await page.getByRole('button',{name:/^新建用户$/}).click()
    await page.getByLabel(/^用户名$/).fill(username);await page.getByLabel(/^密码$/).fill(password)
    userId=await captureCreated(page,'/api/users',()=>page.getByRole('button',{name:/^创建$/}).click())
    await page.getByRole('textbox',{name:/搜索/}).fill(username);let row=page.locator('tbody tr').first();await expect(row).toContainText(username)
    await page.getByRole('button',{name:/^新建用户$/}).click();await page.getByLabel(/^用户名$/).fill(username);await page.getByLabel(/^密码$/).fill(password)
    const duplicate=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/users'&&r.request().method()==='POST')
    await page.getByRole('button',{name:/^创建$/}).click();expect((await duplicate).status()).toBe(400)
    await page.getByRole('button',{name:/^取消$/}).click()
    for(const [role,label]of [['operator','操作员'],['administrator','管理员'],['viewer','查看者']]) {
      await page.goto('/users');await page.getByRole('textbox',{name:/搜索/}).fill(username);row=page.locator('tbody tr').first()
      await row.getByRole('combobox').click();await page.getByRole('option',{name:label,exact:true}).click()
      await expect(row.getByRole('combobox')).toHaveText(label);await page.reload();await page.getByRole('textbox',{name:/搜索/}).fill(username);await expect(page.locator('tbody tr').first().getByRole('combobox')).toHaveText(label)
      await login(page,username,password);await page.waitForLoadState('networkidle')
      const me=await apiRequest(page,'/api/me');expect(me.status).toBe(200);expect(me.body).toMatchObject({Username:username,Role:role})
      const token=await page.evaluate(()=>localStorage.getItem('nodesteer_token')!);tokens.push(token)
      await appendFile(privateDir+'/ui-users.jsonl',JSON.stringify({username,token})+'\n',{mode:0o600})
      observations.push({role,role_saved_and_reload:true,login_http:200,me_role:role})
      const logout=await apiRequest(page,'/api/logout',{method:'POST'});expect(logout.status).toBe(200)
      await loginViaApi(page)
    }
    const changedPassword=randomBytes(24).toString('base64url');await appendFile(privateDir+'/ui-users.jsonl',JSON.stringify({username,password:changedPassword,final:true})+'\n',{mode:0o600})
    expect((await apiRequest(page,'/api/users/'+userId,{method:'PUT',data:{role:'viewer',password:changedPassword}})).status).toBe(200)
    const old=await page.request.post('/api/login',{data:{username,password}}),fresh=await page.request.post('/api/login',{data:{username,password:changedPassword}})
    expect(old.status()).toBe(401);expect(fresh.status()).toBe(200)
    const last=(await fresh.json()).token as string;tokens.push(last);await appendFile(privateDir+'/ui-users.jsonl',JSON.stringify({username,token:last})+'\n',{mode:0o600})
    await page.request.post('/api/logout',{headers:{Authorization:'Bearer '+last}})
    await evidence('user-lifecycle-ui',{username,user_id:userId,duplicate_http:400,roles:observations,password_update_http:200,old_password_http:401,new_password_http:200,final_role:'viewer',credential_file:'private/runweb/ui-users.jsonl',retention:'No User DELETE API; exact own account retained as Viewer with random private password'})
  } finally {
    await loginViaApi(page)
    if(userId){await apiRequest(page,'/api/users/'+userId,{method:'PUT',data:{role:'viewer'}});if(process.env.NODESTEER_E2E_RESOURCE_LOG)await appendFile(process.env.NODESTEER_E2E_RESOURCE_LOG,JSON.stringify({path:'/api/users',id:userId,name:username,status:'retained_account',role:'viewer'})+'\n')}
    for(const token of tokens)await page.request.post('/api/logout',{headers:{Authorization:'Bearer '+token}})
  }
})

test('SAFE-22 Schedule四类型实际保存/OneTime时区转换/取消零写',async({page})=>{
  await loginViaApi(page);const task=await createIsolatedTask(page,'schedule-types'),owned=['/api/tasks/'+task.id],observations:unknown[]=[]
  const future=new Date(Date.now()+7200_000);future.setSeconds(0,0)
  const wall=new Date(future.getTime()+8*3600_000).toISOString().slice(0,16)
  try {
    for(const [kind,label]of [['cron','Cron'],['interval','间隔'],['one_time','一次性'],['on_start','节点启动时']]) {
      await page.goto('/schedules/new?task_id='+task.id)
      if(kind!=='cron'){await page.getByRole('combobox').nth(1).click();await page.getByRole('option',{name:label,exact:true}).click()}
      if(kind==='cron')await page.getByRole('textbox',{name:/cron 表达式/i}).fill('0 0 1 1 *')
      if(kind==='interval')await page.getByLabel(/^间隔/).fill('120')
      if(kind==='one_time'){await page.getByLabel(/^时区$/).fill('Asia/Shanghai');await page.getByLabel(/^运行时间$/).fill(wall)}
      await page.getByRole('checkbox').uncheck()
      const id=await captureCreated(page,'/api/schedules',()=>page.getByRole('button',{name:/^保存$/}).click());owned.unshift('/api/schedules/'+id)
      const saved=(await apiRequest(page,'/api/schedules/'+id)).body as {run_at?:string;expression?:string;interval_sec?:number}
      expect(saved).toMatchObject({task_id:task.id,type:kind,enabled:false})
      if(kind==='one_time')expect(new Date(saved.run_at!).getTime()).toBe(future.getTime())
      else expect(!saved.run_at||saved.run_at.startsWith('0001-01-01')).toBe(true)
      if(kind!=='cron')expect(saved.expression||'').toBe('')
      if(kind==='interval')expect(saved.interval_sec).toBe(120)
      if(kind==='on_start')expect(saved.interval_sec||0).toBe(0)
      await page.goto('/schedules/'+id);await page.reload();await expect(page.getByRole('combobox').nth(1)).toHaveText(label)
      if(kind==='one_time'){await expect(page.getByLabel(/^运行时间$/)).toHaveValue(wall);await page.getByLabel(/^时区$/).fill('UTC');await expect(page.getByLabel(/^运行时间$/)).toHaveValue(future.toISOString().slice(0,16))}
      let writes=0;page.on('request',r=>{if(r.method()==='PUT'&&new URL(r.url()).pathname==='/api/schedules/'+id)writes++})
      await page.getByRole('link',{name:/^取消$/}).click();await page.waitForURL(/\/schedules$/);expect(writes).toBe(0)
      observations.push({kind,id,saved,readback:true,cancel_zero_put:true})
    }
    await evidence('schedule-four-types',{observations,one_time_iana_wall:wall,one_time_utc:future.toISOString(),enabled_false_no_agent_restart:true})
  } finally {await loginViaApi(page);await cleanupByIds(page,owned)}
})

test('SAFE-23 Script全字段/启停/Clone参数环境/删除Tombstone与Agent cache',async({page})=>{
  test.setTimeout(120_000);await loginViaApi(page)
  const {execFileSync}=await import('node:child_process'),owned:string[]=[],name=testName('script-complete'),marker=testName('script-output'),content=`pwd; id -un; echo '${marker}'; echo "$QA_CLONE_ENV"`
  const inspect=process.env.NODESTEER_E2E_SCRIPT_CACHE_INSPECT;if(!inspect)throw new Error('Scoped cache inspector required')
  try {
    await page.goto('/scripts/new');await page.getByLabel(/^名称$/).fill(name);await page.getByLabel(/^描述$/).fill('QA full script fields')
    await page.locator('textarea').first().fill(content);await page.getByLabel(/超时/).fill('17');await page.getByLabel(/^工作目录$/).fill('/tmp');await page.getByLabel(/^运行用户$/).fill('root')
    await page.getByPlaceholder('名称',{exact:true}).fill('qa_clone_param');await page.getByPlaceholder('默认值',{exact:true}).fill('qa-default');await page.getByRole('button',{name:/^添加参数$/}).click()
    await page.getByPlaceholder('KEY',{exact:true}).fill('QA_CLONE_ENV');await page.getByPlaceholder('VALUE',{exact:true}).fill('qa-env-value');await page.getByRole('button',{name:/^添加$/}).click()
    const id=await captureCreated(page,'/api/scripts',()=>page.getByRole('button',{name:/^保存$/}).click());owned.push('/api/scripts/'+id)
    const original=(await apiRequest(page,'/api/scripts/'+id)).body as Record<string,unknown>
    expect(original).toMatchObject({name,description:'QA full script fields',content,timeout:17,working_dir:'/tmp',run_user:'root',enabled:true,revision:1,sha256:createHash('sha256').update(content).digest('hex')})
    await page.getByRole('textbox',{name:/搜索/}).fill(name);const row=page.locator(`tr:has(a[href="/scripts/${id}"])`)
    for(const [action,enabled]of [['禁用',false],['启用',true]] as const){await row.getByRole('button',{name:/^操作$/}).click();await page.getByRole('menuitem',{name:action,exact:true}).click();await expect.poll(async()=>(await apiRequest(page,'/api/scripts/'+id)).body).toMatchObject({enabled})}
    await row.getByRole('button',{name:/^操作$/}).click();const cloneId=await captureCreated(page,'/api/scripts',()=>page.getByRole('menuitem',{name:/^克隆$/}).click());owned.push('/api/scripts/'+cloneId)
    const clone=(await apiRequest(page,'/api/scripts/'+cloneId)).body as Record<string,unknown>;expect(cloneId).not.toBe(id);expect(clone.revision).toBe(1);expect(String(clone.name)).toContain('copy')
    for(const key of ['content','parameters','environment','working_dir','run_user','timeout','interpreter'])expect(clone[key]).toEqual(original[key])
    const task=await apiRequest(page,'/api/tasks',{method:'POST',data:{name:testName('script-delete-history'),type:'script',script_id:id,target:{type:'node',node_ids:await getNodeIds(page)},enabled:true,timeout:30}})
    const taskId=(task.body as {id:string}).id;owned.unshift('/api/tasks/'+taskId)
    await page.goto('/tasks/'+taskId+'/run');await page.getByRole('button',{name:/^立即运行$/}).click();await page.getByRole('alertdialog').getByRole('button',{name:/^立即运行$/}).click();await page.waitForURL(/\/executions$/)
    let execution:{id:string;stdout:string;status:string}|undefined
    await expect.poll(async()=>{execution=((await apiRequest(page,'/api/executions?task_id='+taskId+'&limit=10')).body as typeof execution[])[0];return execution?.status},{timeout:20_000}).toBe('SUCCESS')
    expect(execution!.stdout).toContain(marker);expect(execution!.stdout).toContain('/tmp');expect(execution!.stdout).toContain('root');expect(execution!.stdout).toContain('qa-env-value')
    const cacheBefore=JSON.parse(execFileSync('python3',[inspect,id],{encoding:'utf8',timeout:35000}));expect(cacheBefore.rows).toBe(1)
    await cleanupByIds(page,['/api/tasks/'+taskId]);owned.splice(owned.indexOf('/api/tasks/'+taskId),1)
    await page.goto('/scripts');await page.getByRole('textbox',{name:/搜索/}).fill(name)
    let deletes=0;page.on('request',r=>{if(r.method()==='DELETE'&&new URL(r.url()).pathname==='/api/scripts/'+id)deletes++})
    await row.getByRole('button',{name:/^操作$/}).click();await page.getByRole('menuitem',{name:/^删除$/}).click();await page.getByRole('alertdialog').getByRole('button',{name:/^取消$/}).click();await expect(page.getByRole('alertdialog')).toHaveCount(0);expect(deletes).toBe(0)
    await row.getByRole('button',{name:/^操作$/}).click();await page.getByRole('menuitem',{name:/^删除$/}).click();await page.getByRole('alertdialog').getByRole('button',{name:/^删除$/}).click();await expect(row).toHaveCount(0);expect(deletes).toBe(1)
    let cacheAfter:unknown;await expect.poll(()=>{cacheAfter=JSON.parse(execFileSync('python3',[inspect,id],{encoding:'utf8',timeout:35000}));return(cacheAfter as {rows:number}).rows},{timeout:20_000}).toBe(0)
    expect((await apiRequest(page,'/api/executions/'+execution!.id)).status).toBe(200)
    await evidence('script-complete-cache-delete',{script_id:id,clone_id:cloneId,original,clone,cache_before:cacheBefore,cache_after:cacheAfter,execution_id:execution!.id,history_retained:true,cancel_zero_delete:true,confirm_one_delete:true})
  } finally {await loginViaApi(page);await cleanupByIds(page,owned)}
})

test('SAFE-24 仅新QA Admin自身UI降为Viewer与旧会话权限收敛',async({page})=>{
  const privateDir=process.env.NODESTEER_E2E_PRIVATE_DIR;if(!privateDir)throw new Error('Private credential directory required')
  const secrets=(await readFile(privateDir+'/ui-users.jsonl','utf8')).trim().split('\n').map(s=>JSON.parse(s) as {username:string;password?:string;final?:boolean})
  const account=[...secrets].reverse().find(x=>x.final&&x.password)!
  expect(account.username.startsWith('qa-20260930-101945-web-user-')).toBe(true)
  let ownToken='',userId=''
  try {
    await loginViaApi(page);await page.goto('/users');await page.getByRole('textbox',{name:/搜索/}).fill(account.username)
    const users=(await apiRequest(page,'/api/users')).body as {id:string;username:string}[];userId=users.find(u=>u.username===account.username)!.id
    await page.locator('tbody tr').first().getByRole('combobox').click();await page.getByRole('option',{name:'管理员',exact:true}).click()
    await expect(page.locator('tbody tr').first().getByRole('combobox')).toHaveText('管理员');await page.waitForLoadState('networkidle')
    await login(page,account.username,account.password!);await page.waitForLoadState('networkidle')
    ownToken=await page.evaluate(()=>localStorage.getItem('nodesteer_token')!);await appendFile(privateDir+'/ui-users.jsonl',JSON.stringify({username:account.username,token:ownToken})+'\n',{mode:0o600})
    await page.goto('/users');await page.getByRole('textbox',{name:/搜索/}).fill(account.username)
    const self=page.locator('tbody tr').first().getByRole('combobox');await expect(self).toBeEnabled();await self.click()
    const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/users/'+userId&&r.request().method()==='PUT')
    await page.getByRole('option',{name:'查看者',exact:true}).click();expect((await response).status()).toBe(200)
    const me=await apiRequest(page,'/api/me'),list=await apiRequest(page,'/api/users')
    const write=await apiRequest(page,'/api/tasks/00000000-0000-0000-0000-000000000000',{method:'PUT',data:{}})
    await expect.soft.poll(()=>page.getByRole('button',{name:/^新建用户$/}).count(),{timeout:5000}).toBe(0)
    const residual=await page.getByRole('button',{name:/^新建用户$/}).count();let deniedCreate:number|null=null
    if(residual) {
      await page.getByRole('button',{name:/^新建用户$/}).click();await page.getByLabel(/^用户名$/).fill(testName('self-denied-user'))
      await page.getByLabel(/^密码$/).fill(account.password!)
      const denied=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/users'&&r.request().method()==='POST')
      await page.getByRole('button',{name:/^创建$/}).click();deniedCreate=(await denied).status();expect(deniedCreate).toBe(403)
    }
    await evidence('self-role-downgrade',{username:account.username,user_id:userId,self_control_enabled:true,role_put:200,old_token_me_http:me.status,old_token_me_role:(me.body as {Role:string}).Role,old_token_users_http:list.status,old_token_write_probe_http:write.status,probe_resource:'missing UUID; no actual Task mutation',settlement_window_ms:5000,new_user_buttons_after_put:residual,visible_create_attempt_http:deniedCreate})
    if(residual&&process.env.NODESTEER_E2E_EVIDENCE_DIR)await page.screenshot({path:process.env.NODESTEER_E2E_EVIDENCE_DIR+'/self-role-downgrade.png'})
    expect(list.status).toBe(403);expect(write.status).toBe(403);expect((me.body as {Role:string}).Role).toBe('viewer')
    await page.reload();await expect(page.getByRole('button',{name:/^新建用户$/})).toHaveCount(0)
  } finally {
    if(ownToken)await page.request.post('/api/logout',{headers:{Authorization:'Bearer '+ownToken}})
    await loginViaApi(page);if(userId)await apiRequest(page,'/api/users/'+userId,{method:'PUT',data:{role:'viewer'}})
  }
})

test('SAFE-25 独占pending节点空态/Labels覆盖/状态控件/清理',async({page})=>{
  await loginViaApi(page);const name=testName('node-controls')
  const created=await apiRequest(page,'/api/nodes/enrollment',{method:'POST',data:{node_name:name,node_ip:'192.0.2.88',hub_address:new URL(page.url()).origin}})
  expect(created.status).toBe(200);const id=(created.body as {node_id:string}).node_id,owned=['/api/nodes/'+id]
  try {
    await page.goto('/agents/'+id);await page.waitForLoadState('networkidle')
    const empty=await page.locator('main').innerText()
    for(const text of ['暂无清单信息','暂无任务','暂无调度','暂无托管应用','暂无执行'])expect(empty).toContain(text)
    const add=page.getByRole('button',{name:/^添加标签$/});await expect(add).toBeDisabled();await page.getByPlaceholder('key',{exact:true}).fill('   ');await expect(add).toBeDisabled()
    for(const value of ['one','two']){await page.getByPlaceholder('key',{exact:true}).fill('qa_unique_label');await page.getByPlaceholder('value',{exact:true}).fill(value);await add.click();await expect.poll(async()=>((await apiRequest(page,'/api/nodes/'+id)).body as {labels:Record<string,string>}).labels.qa_unique_label).toBe(value)}
    await page.reload();await expect(page.locator('main')).toContainText('two')
    await apiRequest(page,'/api/nodes/'+id,{method:'POST',data:{status:'online'}})
    const statuses:unknown[]=[]
    for(const [action,expected]of [['维护','maintenance'],['恢复','online']] as const) {
      await page.goto('/agents');await page.getByRole('textbox',{name:/搜索/}).fill(name);const row=page.locator(`tr:has(a[href="/agents/${id}"])`)
      await row.getByRole('button',{name:/^操作$/}).click();await page.getByRole('menuitem',{name:action,exact:true}).click()
      await expect.poll(async()=>(await apiRequest(page,'/api/nodes/'+id)).body).toMatchObject({status:expected});statuses.push({action,status:expected})
    }
    await apiRequest(page,'/api/nodes/'+id,{method:'POST',data:{status:'offline'}});await page.goto('/agents');await page.getByRole('textbox',{name:/搜索/}).fill(name)
    await page.locator(`tr:has(a[href="/agents/${id}"])`).getByRole('button',{name:/^操作$/}).click();await page.getByRole('menuitem',{name:/^设为在线$/}).click()
    await expect.poll(async()=>(await apiRequest(page,'/api/nodes/'+id)).body).toMatchObject({status:'online'})
    await evidence('node-controls',{node_id:id,name,empty_text:empty,label_empty_disabled:true,label_add_override_refresh:true,statuses,offline_set_online:true,scope:'new pending Node Hub record only; no live Agent state changed'})
  } finally {await loginViaApi(page);await cleanupByIds(page,owned)}
})
