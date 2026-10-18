## ADDED Requirements

### Requirement: 同步内容可信后才能改变状态
Agent SHALL 校验同步帧所有 Script 的实际内容与 SHA 一致；任一校验失败 SHALL 拒绝整帧，不改变 Revision、持久对象或物理应用文件。

#### Scenario: 坏 SHA 帧包含删除意图
- **WHEN** 同步帧包含内容与 SHA 不一致的 Script 及 pruned Application
- **THEN** 返回失败确认，旧 Script/Task/Schedule/Application、Revision 与应用文件全部保留；正确重试后才收敛
