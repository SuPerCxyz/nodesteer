# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: full-feature-ui.spec.ts >> FULL 当前页面真实业务补测 >> FULL-06 UI文件中继双目标与客户机文件一致、失败重试取消
- Location: e2e/tests/full-feature-ui.spec.ts:281:3

# Error details

```
Error: expect(received).toMatchObject(expected)

- Expected  - 1
+ Received  + 1

  Object {
-   "status": "CANCELED",
+   "status": "FAILED",
  }

Call Log:
- Timeout 5000ms exceeded while waiting on the predicate
```

# Page snapshot

```yaml
- generic [ref=f6e2]:
  - generic [ref=f6e5]:
    - link "Skip to Main" [ref=f6e6] [cursor=pointer]:
      - /url: "#content"
    - navigation "主导航" [ref=f6e8]:
      - generic [ref=f6e9]:
        - list [ref=f6e11]:
          - listitem [ref=f6e12]:
            - generic [ref=f6e13]:
              - link "NodeSteer NodeSteer Linux 节点自动化控制台" [ref=f6e14] [cursor=pointer]:
                - /url: /
                - generic [ref=f6e15]:
                  - img "NodeSteer" [ref=f6e16]
                  - text: NodeSteer
                - generic [ref=f6e17]: Linux 节点自动化控制台
              - button "切换侧边栏" [ref=f6e18] [cursor=pointer]
        - generic [ref=f6e20]:
          - generic [ref=f6e21]:
            - generic [ref=f6e22]: 概览
            - list [ref=f6e23]:
              - listitem [ref=f6e24]:
                - link "仪表盘" [ref=f6e25] [cursor=pointer]:
                  - /url: /
          - generic [ref=f6e32]:
            - generic [ref=f6e33]: 运行
            - list [ref=f6e34]:
              - listitem [ref=f6e35]:
                - link "任务" [ref=f6e36] [cursor=pointer]:
                  - /url: /tasks
              - listitem [ref=f6e41]:
                - link "调度" [ref=f6e42] [cursor=pointer]:
                  - /url: /schedules
              - listitem [ref=f6e48]:
                - link "执行" [ref=f6e49] [cursor=pointer]:
                  - /url: /executions
              - listitem [ref=f6e53]:
                - link "文件传输" [ref=f6e54] [cursor=pointer]:
                  - /url: /transfers
          - generic [ref=f6e59]:
            - generic [ref=f6e60]: 节点
            - list [ref=f6e61]:
              - listitem [ref=f6e62]:
                - link "节点" [ref=f6e63] [cursor=pointer]:
                  - /url: /agents
              - listitem [ref=f6e68]:
                - link "分组" [ref=f6e69] [cursor=pointer]:
                  - /url: /groups
          - generic [ref=f6e75]:
            - generic [ref=f6e76]: 资源
            - list [ref=f6e77]:
              - listitem [ref=f6e78]:
                - link "脚本" [ref=f6e79] [cursor=pointer]:
                  - /url: /scripts
              - listitem [ref=f6e86]:
                - link "应用" [ref=f6e87] [cursor=pointer]:
                  - /url: /applications
          - generic [ref=f6e91]:
            - generic [ref=f6e92]: 系统
            - list [ref=f6e93]:
              - listitem [ref=f6e94]:
                - link "审计" [ref=f6e95] [cursor=pointer]:
                  - /url: /audit
              - listitem [ref=f6e101]:
                - link "用户" [ref=f6e102] [cursor=pointer]:
                  - /url: /users
              - listitem [ref=f6e109]:
                - link "设置" [ref=f6e110] [cursor=pointer]:
                  - /url: /settings
        - list [ref=f6e116]:
          - listitem [ref=f6e117]:
            - button "qa-repair-20260930-101945-admin qa-repair-20260930-101945-admin 管理员" [ref=f6e118] [cursor=pointer]:
              - img "qa-repair-20260930-101945-admin" [ref=f6e120]
              - generic [ref=f6e121]:
                - generic [ref=f6e122]: qa-repair-20260930-101945-admin
                - generic [ref=f6e123]: 管理员
        - button "Toggle Sidebar" [ref=f6e127]
    - generic [ref=f6e128]:
      - banner [ref=f6e129]:
        - generic [ref=f6e131]:
          - generic [ref=f6e132]:
            - heading "文件传输" [level=1] [ref=f6e133]
            - paragraph [ref=f6e134]: 通过 Hub 将文件从一个 Agent 发送到指定节点。
          - generic [ref=f6e135]:
            - generic [ref=f6e136]: Hub 已连接
            - button "语言" [ref=f6e138] [cursor=pointer]:
              - generic [ref=f6e139]: 中
            - button "切换主题" [ref=f6e140] [cursor=pointer]
            - button "个人资料" [ref=f6e142] [cursor=pointer]:
              - generic "qa-repair-20260930-101945-admin" [ref=f6e144]:
                - img "NodeSteer" [ref=f6e145]
      - main [ref=f6e146]:
        - generic [ref=f6e147]:
          - generic [ref=f6e148]: 新建文件传输
          - generic [ref=f6e150]:
            - generic [ref=f6e151]:
              - generic [ref=f6e152]:
                - text: 源 Agent
                - combobox "源 Agent" [ref=f6e153] [cursor=pointer]:
                  - generic: qa-repair-20260930-101945-native-1
              - generic [ref=f6e154]:
                - text: 源文件路径
                - textbox "源文件路径" [ref=f6e155]:
                  - /placeholder: /var/lib/app/config.yaml
            - generic [ref=f6e157]:
              - generic [ref=f6e158]:
                - text: 目标 Agent
                - combobox "目标 Agent" [ref=f6e159] [cursor=pointer]:
                  - generic: 选择目标 Agent
              - generic [ref=f6e160]:
                - text: 目标文件路径
                - textbox "目标文件路径" [ref=f6e161]:
                  - /placeholder: /var/lib/app/config.yaml
              - button "添加目标" [disabled]
            - button "开始传输" [disabled]
        - generic [ref=f6e162]:
          - generic [ref=f6e163]:
            - generic [ref=f6e164]:
              - textbox "搜索传输 ID、路径或节点" [ref=f6e165]: 32c640ec-0a99-46b3-9987-1cd167954557
              - button "重置" [ref=f6e166] [cursor=pointer]
            - button "视图" [ref=f6e167] [cursor=pointer]
          - table [ref=f6e170]:
            - rowgroup [ref=f6e171]:
              - row [ref=f6e172]:
                - columnheader [ref=f6e173]:
                  - 'button "源 Agent: 升序" [ref=f6e175] [cursor=pointer]':
                    - generic [ref=f6e176]: 源 Agent
                - columnheader [ref=f6e177]:
                  - 'button "源文件路径: 升序" [ref=f6e179] [cursor=pointer]':
                    - generic [ref=f6e180]: 源文件路径
                - columnheader [ref=f6e181]:
                  - 'button "状态: 升序" [ref=f6e183] [cursor=pointer]':
                    - generic [ref=f6e184]: 状态
                - columnheader [ref=f6e185]:
                  - 'button "错误原因: 升序" [ref=f6e187] [cursor=pointer]':
                    - generic [ref=f6e188]: 错误原因
                - columnheader "目标节点" [ref=f6e189]
                - columnheader [ref=f6e191]:
                  - 'button "时间: 升序" [ref=f6e193] [cursor=pointer]':
                    - generic [ref=f6e194]: 时间
                - columnheader "操作" [ref=f6e195]
            - rowgroup [ref=f6e197]:
              - row [ref=f6e198]:
                - cell "qa-repair-20260930-101945-native-1" [ref=f6e199]
                - cell "/tmp/qa-pw2-20261018-relay-source-aa749e34-missing" [ref=f6e200]
                - cell "失败" [ref=f6e202]
                - 'cell "source upload failed: open /tmp/qa-pw2-20261018-relay-source-aa749e34-missing: no such file or directory" [ref=f6e204]'
                - 'cell "qa-repair-20260930-101945-native-2 失败 source upload failed: open /tmp/qa-pw2-20261018-relay-source-aa749e34-missing: no such file or directory" [ref=f6e206]':
                  - generic [ref=f6e208]:
                    - generic [ref=f6e209]:
                      - generic "qa-repair-20260930-101945-native-2" [ref=f6e210]
                      - generic [ref=f6e211]: 失败
                    - 'generic "source upload failed: open /tmp/qa-pw2-20261018-relay-source-aa749e34-missing: no such file or directory" [ref=f6e212]'
                - cell [ref=f6e213]:
                  - time [ref=f6e214]: 刚刚
                - cell [ref=f6e215]:
                  - generic [ref=f6e216]:
                    - link "查看详情" [ref=f6e217] [cursor=pointer]:
                      - /url: /transfers/32c640ec-0a99-46b3-9987-1cd167954557
                    - button "操作" [active] [ref=f6e218] [cursor=pointer]
          - generic [ref=f6e219]:
            - generic [ref=f6e221]:
              - combobox "每页行数" [ref=f6e222] [cursor=pointer]:
                - generic: "10"
              - paragraph [ref=f6e223]: 每页行数
            - generic [ref=f6e224]:
              - generic [ref=f6e225]: 第 1 页，共 1 页
              - generic [ref=f6e226]:
                - button "跳转到第一页" [disabled]
                - button "上一页" [disabled]
                - button "跳转到第 1 页 1" [ref=f6e228] [cursor=pointer]:
                  - generic [ref=f6e229]: 跳转到第 1 页
                  - text: "1"
                - button "下一页" [disabled]
                - button "跳转到最后一页" [disabled]
  - region "Notifications alt+T"
```

# Test source

```ts
  249 |       await page.goto(`/executions/${execId}`)
  250 |       await expect(page.locator('pre')).toContainText(first)
  251 |       await successExecution(page, task.id, second)
  252 |       await expect(page.locator('pre')).toContainText(second, { timeout: 8_000 })
  253 |     } finally { await cleanupByIds(page, [`/api/tasks/${task.id}`]) }
  254 |   })
  255 | 
  256 |   test('FULL-05 Task详情预选调度与on_start字段裁剪', async ({ page }) => {
  257 |     await loginViaApi(page)
  258 |     const task = await createIsolatedTask(page, 'on-start')
  259 |     let id: string | undefined
  260 |     try {
  261 |       await page.goto(`/tasks/${task.id}`)
  262 |       await page.getByRole('link', { name: /新建调度|new schedule/i }).click()
  263 |       await expect(page).toHaveURL(new RegExp(`task_id=${task.id}`))
  264 |       await expect(page.getByRole('combobox').first()).toBeDisabled()
  265 |       await expect(page.getByRole('combobox').first()).toHaveText(task.name)
  266 |       await expect(page.getByRole('combobox').nth(1)).toHaveText(/Cron/i)
  267 |       await page.getByRole('combobox').nth(1).click()
  268 |       await page.getByRole('option', { name: /启动时|on start/i }).click()
  269 |       id = await captureCreated(page, '/api/schedules', () => page.getByRole('button', { name: /^保存$|^save$/i }).click())
  270 |       await page.waitForURL(/\/schedules$/)
  271 |       const schedule = (await apiRequest(page, `/api/schedules/${id}`)).body as Record<string,unknown>
  272 |       expect(schedule.type).toBe('on_start')
  273 |       expect(schedule.task_id).toBe(task.id)
  274 |       expect(schedule.expression || '').toBe('')
  275 |       expect(schedule.interval_sec || 0).toBe(0)
  276 |     } finally {
  277 |       if (id) await cleanupByIds(page, [`/api/schedules/${id}`])
  278 |       await cleanupByIds(page, [`/api/tasks/${task.id}`])
  279 |     }
  280 |   })
  281 |   test('FULL-06 UI文件中继双目标与客户机文件一致、失败重试取消', async ({ page }) => {
  282 |     test.setTimeout(120_000)
  283 |     await loginViaApi(page)
  284 |     const nodes = (await apiRequest(page, '/api/nodes')).body as { id: string; hostname: string; status: string }[]
  285 |     const sourceNode = nodes.find((n) => n.id === (process.env.NODESTEER_E2E_NODE_IDS || '').split(',')[0])!
  286 |     // 目标节点环境驱动：优先 NODESTEER_E2E_TRANSFER_TARGET_IDS，默认取本轮授权节点（NODE_IDS + LABEL_NODE_ID），排除源节点。
  287 |     // 不再按主机名硬编码匹配（旧环境 "agent-2" 在其它环境不存在）。
  288 |     const configuredTargets = (process.env.NODESTEER_E2E_TRANSFER_TARGET_IDS || '').split(',').filter(Boolean)
  289 |     const targetPool = configuredTargets.length ? configuredTargets : authorizedNodeIds()
  290 |     const targets = [...new Set(targetPool)].filter(id => id !== sourceNode?.id).map(id => nodes.find(n => n.id === id)).filter((n): n is typeof nodes[number] => Boolean(n)).slice(0, 2)
  291 |     test.skip(targets.length < 2, '需要至少 2 个授权测试节点作为文件中继目标（NODESTEER_E2E_TRANSFER_TARGET_IDS 或 NODESTEER_E2E_NODE_IDS + NODESTEER_E2E_LABEL_NODE_ID）')
  292 |     expect(targets).toHaveLength(2)
  293 |     const marker = testName('relay-marker')
  294 |     const sourcePath = '/tmp/'+testName('relay-source')
  295 |     const destinationPath = '/tmp/'+testName('relay-target')
  296 |     const tasks: { id: string; target: { type: string; node_ids: string[] }; command: string }[] = []
  297 |     const history: string[] = []
  298 |     try {
  299 |       const source = await apiRequest(page, '/api/tasks', { method: 'POST', data: {
  300 |         name: testName('relay-source-task'), type: 'command', command: `printf '%s\\n' '${marker}' > '${sourcePath}'; cat '${sourcePath}'`, target: { type: 'node', node_ids: [sourceNode.id] }, enabled: true, timeout: 30,
  301 |       } })
  302 |       expect(source.ok).toBeTruthy()
  303 |       const sourceTask = source.body as typeof tasks[number]
  304 |       tasks.push(sourceTask)
  305 |       await runViaUI(page, sourceTask.id)
  306 |       await successExecution(page, sourceTask.id, marker)
  307 |       await page.goto('/transfers')
  308 |       await page.getByRole('combobox').first().click()
  309 |       await page.getByRole('option', { name: sourceNode.hostname, exact: true }).click()
  310 |       await page.getByLabel(/^源文件路径$|^source path$/i).fill(sourcePath)
  311 |       for (const node of targets) {
  312 |         await page.getByRole('combobox').nth(1).click()
  313 |         await page.getByRole('option', { name: node.hostname, exact: true }).click()
  314 |         await page.getByLabel(/^目标文件路径$|^destination path$/i).fill(destinationPath)
  315 |         await page.getByRole('button', { name: /^添加目标$|^add target$/i }).click()
  316 |       }
  317 |       const transferId = await captureCreated(page, '/api/transfers', () => page.getByRole('button', { name: /^开始传输$|^start transfer$/i }).click())
  318 |       history.push(transferId)
  319 |       await expect.poll(async () => (await apiRequest(page, `/api/transfers/${transferId}`)).body, { timeout: 30_000 }).toMatchObject({ status: 'SUCCESS' })
  320 |       const verify = await apiRequest(page, '/api/tasks', { method: 'POST', data: {
  321 |         name: testName('relay-verify-task'), type: 'command', command: `cat '${destinationPath}'`, target: { type: 'node', node_ids: targets.map((n) => n.id) }, enabled: true, timeout: 30,
  322 |       } })
  323 |       expect(verify.ok).toBeTruthy()
  324 |       const verifyTask = verify.body as typeof tasks[number]
  325 |       tasks.push(verifyTask)
  326 |       await runViaUI(page, verifyTask.id)
  327 |       await expect.poll(async () => {
  328 |         const executions = (await apiRequest(page, `/api/executions?task_id=${verifyTask.id}&limit=10`)).body as { node_id: string; status: string; stdout: string }[]
  329 |         return executions.filter((e) => e.status === 'SUCCESS' && e.stdout.includes(marker)).map((e) => e.node_id).sort()
  330 |       }, { timeout: 30_000 }).toEqual(targets.map((n) => n.id).sort())
  331 |       await page.goto('/transfers')
  332 |       await page.getByRole('combobox').first().click()
  333 |       await page.getByRole('option', { name: sourceNode.hostname, exact: true }).click()
  334 |       await page.getByLabel(/^源文件路径$|^source path$/i).fill(sourcePath+'-missing')
  335 |       await page.getByRole('combobox').nth(1).click()
  336 |       await page.getByRole('option', { name: targets[0].hostname, exact: true }).click()
  337 |       await page.getByLabel(/^目标文件路径$|^destination path$/i).fill(destinationPath+'-missing')
  338 |       await page.getByRole('button', { name: /^添加目标$|^add target$/i }).click()
  339 |       const failedId = await captureCreated(page, '/api/transfers', () => page.getByRole('button', { name: /^开始传输$|^start transfer$/i }).click())
  340 |       history.push(failedId)
  341 |       await expect.poll(async () => (await apiRequest(page, `/api/transfers/${failedId}`)).body, { timeout: 20_000 }).toMatchObject({ status: 'FAILED' })
  342 |       await page.getByRole('textbox', { name: /搜索|search/i }).fill(failedId)
  343 |       const row = page.locator('table tbody tr').first()
  344 |       await row.getByRole('button', { name: /^操作$|^actions$/i }).click()
  345 |       await page.getByRole('menuitem', { name: /^重试$|^retry$/i }).click()
  346 |       await expect.poll(async () => (await apiRequest(page, `/api/transfers/${failedId}`)).body, { timeout: 20_000 }).toMatchObject({ status: 'FAILED' })
  347 |       await row.getByRole('button', { name: /^操作$|^actions$/i }).click()
  348 |       await page.getByRole('menuitem', { name: /^取消传输$|^cancel( transfer)?$/i }).click()
> 349 |       await expect.poll(async () => (await apiRequest(page, `/api/transfers/${failedId}`)).body).toMatchObject({ status: 'CANCELED' })
      |                                                                                                  ^ Error: expect(received).toMatchObject(expected)
  350 |     } finally {
  351 |       for (let index=0;index<tasks.length;index++) {
  352 |         const task=tasks[index], file=index===0?sourcePath:destinationPath, cleaned=testName('cleaned')
  353 |         await apiRequest(page, `/api/tasks/${task.id}`, { method: 'PUT', data: { ...task, command: `rm -f -- '${file}' '${file}-missing'; echo '${cleaned}'` } })
  354 |         await apiRequest(page, `/api/tasks/${task.id}/run`, { method: 'POST', data: {} })
  355 |         await successExecution(page, task.id, cleaned)
  356 |       }
  357 |       await cleanupByIds(page, tasks.map((t) => `/api/tasks/${t.id}`).reverse())
  358 |       if (process.env.NODESTEER_E2E_RESOURCE_LOG) {
  359 |         const { appendFile } = await import('node:fs/promises')
  360 |         for (const id of history) await appendFile(process.env.NODESTEER_E2E_RESOURCE_LOG, `${JSON.stringify({ path:'/api/transfers',id,status:'retained_history',source_path:sourcePath,destination_path:destinationPath })}\n`)
  361 |       }
  362 |     }
  363 |   })
  364 | 
  365 |   test('FULL-07 Shell/Bash/Python UI字段、环境/参数、Revision与Clone独立', async ({ page }) => {
  366 |     test.setTimeout(120_000)
  367 |     await loginViaApi(page)
  368 |     const nodeId=(await getNodeIds(page))[0]
  369 |     const owned:string[]=[]
  370 |     try {
  371 |       for(const [interpreter,option] of [['shell','Shell'],['bash','Bash'],['python','Python']]) {
  372 |         const marker=testName(interpreter+'-env'), name=testName(interpreter+'-script')
  373 |         await page.goto('/scripts/new')
  374 |         await page.getByLabel(/^名称$|^name$/i).fill(name)
  375 |         await page.getByRole('combobox').first().click()
  376 |         await page.getByRole('option',{name:option,exact:true}).click()
  377 |         await page.locator('textarea').first().fill(interpreter==='python'?'import os\nprint(os.environ["QA_WEB_VALUE"])':`printf '%s\\n' "$QA_WEB_VALUE"`)
  378 |         await page.getByLabel(/^工作目录$|^working directory$/i).fill('/tmp')
  379 |         await page.getByLabel(/^运行用户$|^run user$/i).fill('root')
  380 |         await page.getByPlaceholder('名称',{exact:true}).fill('qa_text')
  381 |         await page.getByPlaceholder('默认值',{exact:true}).fill('qa-default')
  382 |         await page.getByRole('button',{name:/^添加参数$/}).click()
  383 |         await page.getByPlaceholder('KEY',{exact:true}).fill('QA_WEB_VALUE')
  384 |         await page.getByPlaceholder('VALUE',{exact:true}).fill(marker)
  385 |         await page.getByRole('button',{name:/^添加$/}).click()
  386 |         const scriptId=await captureCreated(page,'/api/scripts',()=>page.getByRole('button',{name:/^保存$|^save$/i}).click())
  387 |         owned.push(`/api/scripts/${scriptId}`)
  388 |         await page.waitForURL(/\/scripts$/)
  389 |         const script=(await apiRequest(page,`/api/scripts/${scriptId}`)).body as Record<string,unknown>
  390 |         expect(script).toMatchObject({interpreter,working_dir:'/tmp',run_user:'root',environment:{QA_WEB_VALUE:marker},parameters:[{name:'qa_text',default:'qa-default',type:'string'}]})
  391 |         const taskResponse=await apiRequest(page,'/api/tasks',{method:'POST',data:{name:testName(interpreter+'-task'),type:'script',script_id:scriptId,target:{type:'node',node_ids:[nodeId]},timeout:30,enabled:true}})
  392 |         expect(taskResponse.ok).toBeTruthy()
  393 |         const taskId=(taskResponse.body as {id:string}).id
  394 |         owned.unshift(`/api/tasks/${taskId}`)
  395 |         await runViaUI(page,taskId)
  396 |         const execution=await successExecution(page,taskId,marker)
  397 |         await page.goto(`/executions/${execution.id}`)
  398 |         await expect(page.locator('pre')).toContainText(marker)
  399 |         await page.goto(`/scripts/${scriptId}`)
  400 |         const updated=marker+'-revision2'
  401 |         await page.locator('textarea').first().fill(interpreter==='python'?`print("${updated}")`:`echo '${updated}'`)
  402 |         await page.getByRole('button',{name:/^保存$|^save$/i}).click()
  403 |         await page.waitForURL(/\/scripts$/)
  404 |         await page.getByRole('textbox',{name:/搜索脚本|search scripts/i}).fill(name)
  405 |         const row=page.locator(`tr:has(a[href="/scripts/${scriptId}"])`)
  406 |         await row.getByRole('button',{name:/^操作$|^actions$/i}).click()
  407 |         const cloneId=await captureCreated(page,'/api/scripts',()=>page.getByRole('menuitem',{name:/^克隆$|^clone$/i}).click())
  408 |         owned.push(`/api/scripts/${cloneId}`)
  409 |         const original=(await apiRequest(page,`/api/scripts/${scriptId}`)).body as {revision:number;content:string}
  410 |         const clone=(await apiRequest(page,`/api/scripts/${cloneId}`)).body as {revision:number;content:string}
  411 |         expect(original.revision).toBe(2)
  412 |         expect(clone.revision).toBe(1)
  413 |         expect(clone.content).toBe(original.content)
  414 |         await page.goto(`/scripts/${scriptId}`)
  415 |         await expect(page.locator('main')).toContainText(/版本历史|revision history/i)
  416 |         const revisions=(await apiRequest(page,`/api/scripts/${scriptId}/revisions`)).body as {revision:number}[]
  417 |         expect(revisions.map(r=>r.revision).sort()).toEqual([1,2])
  418 |       }
  419 |     } finally {await cleanupByIds(page,owned)}
  420 |   })
  421 | 
  422 |   for(const viaTask of [false,true]) test(viaTask?'FULL-09 App Task UI创建/运行Deploy与四操作真实Unit/config/SHA/健康':'FULL-08 真实应用UI Deploy/Stop/Start/Restart/Upgrade与Unit/config/SHA', async ({ page }) => {
  423 |     test.setTimeout(240_000)
  424 |     const { execFileSync } = await import('node:child_process')
  425 |     const { readFile, writeFile, appendFile } = await import('node:fs/promises')
  426 |     await loginViaApi(page)
  427 |     const nodeId=(await getNodeIds(page))[0]
  428 |     const nodes=(await apiRequest(page,'/api/nodes')).body as {id:string;hostname:string}[]
  429 |     const node=nodes.find(n=>n.id===nodeId)!
  430 |     const elf=process.env.NODESTEER_E2E_APP_ELF, inspector=process.env.NODESTEER_E2E_GUEST_INSPECT, base=process.env.NODESTEER_E2E_APP_DIR
  431 |     // 需要授权客户机 SSH + ELF/检查器夹具（红线外）：未提供时显式 skip 并说明，不再以报错形式失败。
  432 |     test.skip(!elf||!inspector||!base, '需要授权客户机 SSH 与 ELF/检查器夹具（NODESTEER_E2E_APP_ELF / NODESTEER_E2E_GUEST_INSPECT / NODESTEER_E2E_APP_DIR），本轮红线外')
  433 |     if(!elf||!inspector||!base) return
  434 |     const bytes=await readFile(elf)
  435 |     const {createHash}=await import('node:crypto')
  436 |     const expectedSHA=createHash('sha256').update(bytes).digest('hex')
  437 |     const bytesV2=Buffer.concat([bytes,Buffer.from('\nqa-elf-version-v2\n')])
  438 |     const expectedSHAV2=createHash('sha256').update(bytesV2).digest('hex')
  439 |     const artifactNameV2=testName('elf-v2')
  440 |     const nonce=randomUUID().slice(0,8), unit=`${TEST_PREFIX}web-app-${nonce}.service`,directory=`${base}/app-${nonce}`
  441 |     const applicationName=testName('service'), owned:string[]=[], observations:unknown[]=[]
  442 |     let appId:string|undefined,bodyFailure:unknown,cleanupFailure:unknown
  443 |     try {
  444 |       await page.goto('/artifacts/new')
  445 |       await page.getByLabel(/^名称$|^name$/i).fill(testName('elf'))
  446 |       await page.getByLabel(/^版本$|^version$/i).fill('v1')
  447 |       await page.locator('input[type=file]').setInputFiles(elf)
  448 |       const artifactId=await captureCreated(page,'/api/artifacts',()=>page.getByRole('button',{name:/^上传$|^upload$/i}).click())
  449 |       owned.push(`/api/artifacts/${artifactId}`)
```