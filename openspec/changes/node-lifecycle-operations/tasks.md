## 1. 参数自动生成与上报（批 1 · Go）

- [x] 1.1 `build.yml` 与 Makefile 增加 `-X main.version` 注入；`cmd/agent` 版本变量与回退默认值。
- [x] 1.2 `deployment_mode` 自动探测（容器/native，显式配置优先）；`host_integration` 由模式推导。
- [x] 1.3 纳管命令与配置模板去除 `deployment_mode`/`host_integration`/`agent_version`/`data_dir` 硬编码键（旧配置向后兼容）。
- [x] 1.4 Hub 对 HELLO 上报 `deployment_mode` 做枚举白名单校验。
- [x] 1.5 测试：版本注入回退、探测优先级、模板无硬编码键、非法枚举拒绝/归一化。

## 2. 暂停完善 + 离线交互锁定（批 1 · Go）

- [x] 2.1 Hub 暂停节点补拦文件传输与制品预取（settings/变更通知保持不拦）。
- [x] 2.2 调度因暂停跳过时创建 SKIPPED 执行记录（含节点与原因）。
- [x] 2.3 Agent 暂停态拒收执行/部署指令并回报。
- [x] 2.4 测试锁定：暂停期已部署常驻服务不受影响、运行中任务不中止、未暂停时离线调度正常（现状回归）、各下发路径拦截矩阵。

## 3. 自升级 Go 链（批 2 · Go）

- [x] 3.1 协议与执行模型新增 `agent_upgrade` 类型（消息、能力映射、执行历史）。
- [x] 3.2 Hub 批量/单点升级 API（复用 `node_ids` 数组先例）与下发。
- [x] 3.3 Agent 升级执行：下载（binary 端点）→ SHA256 校验 → 备份 `os.Executable` → 替换 → 重启服务 → 失败回滚。
- [x] 3.4 升级成功重连后版本上报更新；docker 形态拒绝升级。
- [x] 3.5 测试：升级全链成功、校验失败回滚、重启失败回滚、版本上报更新、docker 拒绝。

## 4. 批量 UI（批 3 · Web，依赖批 1–3）

- [x] 4.1 节点列表多选基建（rowSelection + 既有 bulk-actions 组件接入）。
- [x] 4.2 批量升级入口（native 可选、docker 隐藏）与逐节点结果反馈。
- [x] 4.3 批量暂停/恢复入口与整体结果反馈。
- [x] 4.4 前端 tsc/lint/build/vitest 全绿 + 浏览器截图核对。

## 5. 验证与同步

- [x] 5.1 Go：gofmt/vet/test/race 全绿；Web：tsc/lint/format/build/vitest 全绿。
- [x] 5.2 `openspec validate --all` 通过。
- [ ] 5.3 现网按需部署更新（另行授权）。
