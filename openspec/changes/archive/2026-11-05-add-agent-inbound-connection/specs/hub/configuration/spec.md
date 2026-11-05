## ADDED Requirements

### Requirement: 入站连接配置与默认关闭

Hub SHALL 提供显式的入站连接配置，包括 Hub CA、Hub 客户端证书/私钥、拨号开关、拨号并发/超时/退避、允许的端口和 CIDR 策略。未显式启用入站功能时，Hub SHALL 正常启动但禁用入站 bundle 签发和拨号，并记录明确原因。启用后若未提供 CA/客户端证书，Hub SHALL 在本地数据目录生成并持久化 CA 与客户端证书（私钥 0600），或加载显式配置的证书；不得监听明文端口。Agent SHALL 通过独立配置启用入站 listener，未安装 bundle 时不得监听。

#### Scenario: 默认未启用

- **WHEN** Hub 启动时没有启用入站功能
- **THEN** Hub 正常启动，入站功能显示为禁用
- **AND** 添加节点仍可用，但 bundle 下载返回明确的不可用错误，Hub 不拨号任何节点

#### Scenario: 启用并自动生成 CA

- **WHEN** Administrator 启用入站功能但没有提供 CA/客户端证书路径
- **THEN** Hub 在本地数据目录生成 CA、客户端证书和私钥（0600），并记录证书指纹
- **AND** Hub 可以为节点签发 bundle，并按策略拨号已签发 bundle 的节点

#### Scenario: 显式配置错误

- **WHEN** Administrator 显式配置了不可读、不匹配或过期的 CA/客户端证书
- **THEN** Hub 启动失败并返回非敏感错误
- **AND** 不会回退到自动生成或明文连接

#### Scenario: Agent 未安装 bundle

- **WHEN** Agent 没有入站证书、私钥或令牌
- **THEN** Agent 不启动入站 listener，仅运行既有出站连接
- **AND** 不会退化为明文 HTTP 或跳过证书校验

### Requirement: 拨号安全策略配置

Hub SHALL 允许配置拨号并发上限、单节点退避、握手超时、允许的 endpoint 端口和拒绝的 CIDR。默认 SHALL 仅允许端口 `18444`，SHALL 拒绝 loopback、link-local、云 metadata、multicast 和 unspecified 地址；RFC1918 地址 SHALL 默认允许以支持内网 Hub/Agent。Agent SHALL 允许配置 `inbound_listen_addr` 和 `inbound_allowed_source_cidrs`。

#### Scenario: 非默认端口

- **WHEN** 节点 endpoint 使用了未被 `inbound_allowed_ports` 允许的端口
- **THEN** Hub 拒绝保存或拒绝拨号，并返回明确错误
- **AND** 不会把 Hub 当作任意端口扫描器

#### Scenario: 禁止的目标地址

- **WHEN** endpoint 解析到 loopback、link-local、云 metadata、multicast 或 unspecified 地址
- **THEN** Hub 不建立 TCP 连接，并记录被拒绝的阶段和地址类别
- **AND** 该失败不会泄露到普通节点状态以外的信息

#### Scenario: 内网地址

- **WHEN** Hub 与 Agent 位于同一内网，endpoint 为 RFC1918 地址
- **THEN** 默认策略允许拨号，除非管理员通过拒绝 CIDR 显式排除

#### Scenario: Agent 限制来源

- **WHEN** Agent 配置了 `inbound_allowed_source_cidrs` 且 Hub 来源不在其中
- **THEN** Agent 在 TLS 握手前或握手阶段拒绝连接
- **AND** 不会执行 HUB_HELLO 认证或数据面请求
