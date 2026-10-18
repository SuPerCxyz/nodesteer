## ADDED Requirements

### Requirement: Host 文件映射必须对应真实宿主
Host Integration 文件操作 SHALL 在创建目录或写入前证明映射对应请求的真实宿主路径；缺失、错误、只读或无法确认的映射 SHALL 明确失败，保留 allowlist、symlink 与越界限制。

#### Scenario: 错误与正确 bind
- **WHEN** Relay 的目标挂载缺失或绑定错误目录
- **THEN** 传输失败且真实宿主、错误宿主目录和容器覆盖层均无本次写入；正确映射时真实宿主文件 SHA 与结果一致
