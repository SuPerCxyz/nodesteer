## ADDED Requirements

### Requirement: 节点显示名

Hub SHALL 为每个节点提供独立的 `display_name` 字段，默认空字符串，用于用户可见名称。`display_name` SHALL NOT 参与节点身份、Agent ID、hostname 匹配、权限、执行目标或路由。创建、重新注册、HELLO 元数据刷新、重新纳管和普通 Upsert SHALL NOT 覆盖已设置的 `display_name`。

#### Scenario: 新节点默认空

- **WHEN** 创建或纳管一个新节点
- **THEN** `display_name` 为空字符串
- **AND** API/UI 在显示时回退到 `hostname`

#### Scenario: 重新注册保留显示名

- **WHEN** Agent 以已有身份重新注册并上报新的 hostname
- **THEN** `hostname` 按既有语义更新
- **AND** `display_name` 保持不变

#### Scenario: 重新纳管保留显示名

- **WHEN** 管理员对既有节点重新生成纳管命令
- **THEN** 节点记录被复用，`display_name` 不被清空
- **AND** 只有显式重命名请求可以修改或清除它

#### Scenario: 显示名不参与身份

- **WHEN** 两个节点具有相同 `display_name`
- **THEN** 系统仍按 node ID / Agent ID 区分它们
- **AND** 不因重名拒绝创建、执行或查询

### Requirement: 显示名校验与更新 API

Hub SHALL 在节点 API 中返回 `display_name`（空值返回空字符串），并 SHALL 支持通过单节点更新接口 `POST/PUT /api/nodes/{id}` 设置或清除该字段；批量 `POST /api/nodes` SHALL NOT 接受 `display_name`。更新 SHALL 在去首尾空白后按 Unicode 码点限制为最多 64 个字符，拒绝控制字符；空字符串 SHALL 表示清除。更新不存在的节点 SHALL 返回 404，非法输入 SHALL 返回 400 且不得产生部分修改。

#### Scenario: 设置显示名

- **WHEN** Operator 提交合法的 `display_name`
- **THEN** 节点详情和列表 API 返回新值
- **AND** `hostname` 保持 Agent 上报值不变

#### Scenario: 清除显示名

- **WHEN** 用户提交空字符串或仅空白
- **THEN** `display_name` 变为空
- **AND** UI 回退显示 hostname

#### Scenario: 超长或控制字符

- **WHEN** 提交超过 64 个码点或包含控制字符的值
- **THEN** 返回 400，节点原显示名不变
- **AND** 不写入部分字段或审计成功记录

#### Scenario: 节点不存在

- **WHEN** 对不存在的 node ID 提交 `display_name`
- **THEN** 返回 404
- **AND** 不创建节点或审计成功记录

#### Scenario: 相同值重复提交

- **WHEN** 提交与当前值相同的 `display_name`
- **THEN** 请求成功但不产生重复的重命名审计
- **AND** 不触发无关的节点更新副作用
