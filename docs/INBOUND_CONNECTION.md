# 入站连接 Runbook

适用拓扑：Agent 无法主动访问 Hub，但 Hub 可以出站访问 Agent 的 `endpoint`（默认 TCP 18444）。双方隔离 NAT、relay/P2P、证书自动轮换不在当前范围。

## 1. 首次部署

1. Hub 配置 `inbound_enabled: true`，确认 `inbound_allowed_ports` 包含 18444；未提供 CA 路径时，Hub 会在 `<data_dir>/inbound` 生成 PKI。
2. 备份 `<data_dir>/inbound/ca-key.pem` 和 `client-key.pem`，保持 `0600`，不要提交到版本库。
3. 添加节点时填写节点域名或 IP；未写端口按 18444 规范化。
4. 在节点详情下载 `nodesteer-inbound-<node>.tar.gz`，复制到 Agent 主机。
5. `tar xzf ... && sudo ./install-inbound.sh`。脚本要求 `nodesteer-agent` 已安装。
6. Agent 防火墙只放行 Hub 出口地址：

   ```bash
   ufw allow from <Hub 出口 IP> to any port 18444 proto tcp
   ```

   Docker Agent 需要 `-p 18444:18444`，并确认 bundle 的 `listen_addr` 为 `0.0.0.0:18444`。

## 2. 验证

- 节点详情点击「测试连接」：依次检查 `dns`、`tcp`、`tls`、`pin`、`hub_hello`；测试不注册会话，每节点 10 秒最多一次。
- 成功后节点显示 `inbound`，诊断区显示证书指纹、有效期和最近握手时间。
- 失败阶段码：
  - `dns_failed`：Hub 无法解析 endpoint。
  - `endpoint_denied`：目标地址被 CIDR/端口策略拒绝。
  - `tcp_failed`：18444 未放行或 Agent 未监听。
  - `tls_verify_failed`：CA、SAN、EKU 或客户端证书不匹配。
  - `inbound_pin_mismatch`：Agent 证书与 Hub 记录不一致，通常需要重新下载 bundle。
  - `inbound_auth_failed`：令牌或 `HUB_HELLO` 校验失败。

## 3. 轮换与过期

1. 在节点详情点击「轮换入站身份」，或调用 `POST /api/nodes/{id}/inbound-bundle/rotate`。
2. 保存返回的压缩包，在 Agent 主机重新解压并执行 `install-inbound.sh`。
3. 轮换立即关闭旧入站会话并使旧令牌失效；旧压缩包不能再次下载。
4. Hub 不自动轮换证书。到期前手动轮换；`cert_not_after` 可在节点详情查看。

## 4. 回滚

```bash
sudo systemctl stop nodesteer-agent
sudo rm -rf /etc/nodesteer/inbound
# 删除 /etc/nodesteer/agent.yaml 中的 inbound 段
sudo systemctl start nodesteer-agent
```

Hub 侧随后轮换或禁用该节点入站身份。若只是临时恢复出站连接，可保留证书但停止入站 listener；确认 Agent 能主动访问 Hub。

## 5. 日志与审计

- Hub 日志字段：`node`、`endpoint`、`stage`、`failures`、`duration`；不包含令牌或私钥。
- 审计动作：`node.inbound_bundle_issue`、`node.inbound_bundle_rotate`、`node.connection_test`。
- Agent 日志不输出证书私钥或令牌；失败按阶段返回稳定错误码。

## 6. 已知限制

- 入站与出站同时可用时优先复用出站会话；无出站时 Hub 自动拨号。
- 入站数据面不支持的操作会返回 `409 inbound_data_plane_unavailable` / `inbound_stage_unavailable` 或落 `BLOCKED`，不会静默等待。
- 对端隔离 NAT、无端口转发的双方私网拓扑暂不支持。
