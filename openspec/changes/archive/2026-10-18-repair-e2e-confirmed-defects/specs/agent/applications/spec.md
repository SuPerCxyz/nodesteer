## ADDED Requirements

### Requirement: 部署恢复不得破坏未替换的旧版
Agent SHALL 在停止或替换旧版前可靠保存完整恢复信息；尚未变更旧文件的 PREPARING 恢复 SHALL 保留旧 binary、config、unit。恢复无法确认或失败 SHALL 明确失败，不得以空备份推定首次安装并删除旧版。

#### Scenario: 各部署阶段崩溃
- **WHEN** 升级在准备、停止、替换或验证阶段崩溃并重启
- **THEN** 旧版文件及服务可恢复，或明确报告无法恢复的错误，执行不得遗留伪成功

#### Scenario: 首次安装失败
- **WHEN** 无旧版的独占首次部署失败
- **THEN** 仅清理本次创建的文件，且有缺失备份时不得删除无法确认归属的旧文件

### Requirement: Artifact 下载使用配置的 TLS 信任
Agent SHALL 对预取和部署 Artifact 使用已配置的私有 CA、认证与 SHA 校验。

#### Scenario: 私有 CA 发布包
- **WHEN** HTTPS Artifact 使用配置所信任的私有 CA
- **THEN** 预取及真实部署成功；缺失或错误 CA 时明确失败且不留下有效缓存或临时文件

### Requirement: Host 托管服务操作真实有效
Host Integration SHALL 在现有最小权限与挂载条件下操作真实宿主的托管 Unit，错误权限或环境 SHALL 明确失败。

#### Scenario: 宿主 systemd 生命周期
- **WHEN** 执行现有托管应用部署、启动、停止、升级、重启或内部回滚/reload
- **THEN** 真实宿主 Unit 与文件达到请求状态，缺少必要条件时不报告伪成功
