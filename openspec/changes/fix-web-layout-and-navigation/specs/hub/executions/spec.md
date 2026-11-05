## ADDED Requirements
### Requirement: 按来源调度精确过滤执行
GET /api/executions SHALL 支持可选 schedule_id 精确过滤，可组合 node_id；列表和总数使用同一过滤条件。不传或空值 SHALL 保持原行为。
#### Scenario: 混合来源与分页
- **WHEN** 同任务具有多调度和手动执行，按 schedule_id 与可选 node_id 分页查询
- **THEN** 仅返回精确匹配记录，总数与分页一致，手动及其他调度不会混入
