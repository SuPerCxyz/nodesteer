## ADDED Requirements

### Requirement: 节点重命名权限与审计

Hub SHALL 允许 Administrator 和 Operator 修改节点的 `display_name`，Viewer SHALL 只读。Hub SHALL 在值实际变化时记录 `node.rename` 审计，包含节点 ID、旧值、新值、操作者和时间；审计 SHALL NOT 包含 Agent credential、令牌或其他秘密。Viewer 的写请求 SHALL 返回 403，且不得产生部分更新或成功审计。

#### Scenario: Operator 重命名

- **WHEN** Operator 提交合法的 `display_name`
- **THEN** 更新成功并记录 `node.rename` 审计
- **AND** 审计包含 old/new 和操作者

#### Scenario: Viewer 只读

- **WHEN** Viewer 提交 `display_name`
- **THEN** 返回 403，节点名称不变
- **AND** 不写入成功审计

#### Scenario: 未变化不审计

- **WHEN** 用户提交与当前值相同的显示名
- **THEN** 请求成功但不新增 `node.rename`
- **AND** 不产生重复审计噪声

#### Scenario: 与节点其他更新并存

- **WHEN** 同一请求同时更新 status/labels 与 display_name
- **THEN** 所有字段先完成权限与格式校验，再原子更新
- **AND** 任一字段非法时不产生部分修改，重命名审计只在值实际变化时记录

#### Scenario: 审计不含秘密

- **WHEN** 查询重命名审计
- **THEN** 只看到节点 ID、旧/新显示名、操作者和时间
- **AND** 不包含 credential、令牌、证书或私钥
