## ADDED Requirements

### Requirement: Secret 跨日志块保密
Agent SHALL 在 stdout/stderr 的实时和终态日志中遮蔽执行 Secret，分块、重试、取消和超时不得使秘密可重组；普通日志 SHALL 保持可用且最终尾部不丢失。

#### Scenario: 拆块与交错输出
- **WHEN** Secret 跨块、逐字节或跨大块边界输出且两流交错
- **THEN** 实时 API/UI、持久日志与终态输出均不包含可重组明文，且流顺序和非 Secret 内容完整

### Requirement: Remote 条件有效性
Agent SHALL 仅使用具有有效观察时间和正 TTL 且未过期的 Remote State；缺失、过期、非法或不可确认状态 SHALL 导致 UNKNOWN/BLOCKED。

#### Scenario: 无效 Remote 响应
- **WHEN** Remote 条件响应过期、TTL 非正、时间无效或请求失败
- **THEN** 任务 BLOCKED 且没有业务执行效果；有效响应仍可正常执行
