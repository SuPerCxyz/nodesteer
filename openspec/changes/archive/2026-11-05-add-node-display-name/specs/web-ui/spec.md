## ADDED Requirements

### Requirement: 统一节点显示名

Web UI SHALL 通过统一的 `nodeDisplayName` 规则显示节点名称：优先非空 `display_name`，否则非空 `hostname`，否则 node ID，全部为空时使用通用未知节点文案。该规则 SHALL 应用于节点列表/详情、执行、告警、文件传输、概览、监控和目标选择等所有用户可见节点名称。节点详情 SHALL 为 Operator/Administrator 提供重命名入口，Viewer SHALL 只读。

#### Scenario: 显示名优先

- **WHEN** 节点同时有 `display_name` 和 `hostname`
- **THEN** 所有用户可见位置显示 `display_name`
- **AND** 仍可用 hostname/node ID 作为辅助信息或搜索字段

#### Scenario: 空值回退

- **WHEN** `display_name` 为空或仅空白
- **THEN** 显示 `hostname`
- **AND** hostname 也为空时显示 node ID 或未知节点文案

#### Scenario: 页面一致性

- **WHEN** 用户依次查看节点列表、节点详情、执行记录、告警、文件传输和概览
- **THEN** 同一节点在这些页面显示同一个名称
- **AND** 不会有的页面显示显示名、有的页面仍显示旧 hostname

#### Scenario: 重命名入口

- **WHEN** Operator 或 Administrator 打开节点详情
- **THEN** 可以看到重命名操作，弹窗预填当前显示名并显示 64 字计数
- **AND** 提交空值表示清除显示名，提交成功后相关页面刷新

#### Scenario: Viewer 只读

- **WHEN** Viewer 打开节点详情
- **THEN** 可以查看显示名但看不到可执行的重命名操作
- **AND** 直接调用更新接口仍被后端拒绝

#### Scenario: 重命名后回退

- **WHEN** 用户清除显示名
- **THEN** 所有页面立即回退显示 hostname
- **AND** 不需要刷新或重新登录
