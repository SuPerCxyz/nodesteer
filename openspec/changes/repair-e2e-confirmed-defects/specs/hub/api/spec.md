## ADDED Requirements

### Requirement: 全集合过滤后分页
Execution/Audit SHALL 支持兼容旧请求的可选 q，Execution SHALL 支持可选 trigger_type；过滤 SHALL 先于分页且总数使用相同条件。q SHALL 对现有可搜索字段作字面子串匹配，百分号和下划线为普通字符。

#### Scenario: 跨页组合搜索
- **WHEN** 查询非当前页的 Execution ID、Task 名称或 Audit 显示字段，并组合状态或触发类型
- **THEN** 返回所有符合条件的分页结果及准确总数；清除条件恢复原分页行为，不拉取全数据库到浏览器
