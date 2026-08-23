# 03｜Skill 管理平台与工具建设蓝图

## 产品定位

建设“企业 Skill Control Plane”：一个登记、评审、分发、授权、运行控制与审计的统一控制面。它不替代 GitHub、IAM、Secret Manager、SIEM 或源业务系统，而是连接并强制这些系统已有的控制。

## 用户与核心旅程

| 用户 | 主要界面 | 核心动作 |
| --- | --- | --- |
| Skill 所有者 | Developer Portal | 创建登记卡、提交版本、查看测试/运行结果、申请续期 |
| 安全与数据审批人 | Review Queue | 审核数据流、权限、网络、威胁模型和例外 |
| 平台管理员 | Admin Console | 配置分发目录、策略、连接器、环境、紧急停用 |
| 运行审批人 | Approval Inbox | 审核高危执行计划、差异和回滚方式 |
| 审计与合规 | Audit Explorer | 检索不可篡改事件、导出调查证据与保留记录 |

## 平台架构

```text
GitHub / GitLab PR
  → CI 安全门禁（schema、secret、SCA、SBOM、eval）
  → Artifact Registry（版本、签名、hash、元数据）
  → Policy Engine（RBAC、ABAC、风险规则、例外）
  → Distribution Gateway（ChatGPT/Codex、CLI、IDE、内部 Agent）

SSO / SCIM ───────────────→ Identity & Group Service
Vault / KMS ──────────────→ Ephemeral Credential Broker
Connector / MCP Gateway ─→ Action + Data Policy Enforcement
Sandbox Runtime ──────────→ Workspace / Egress / Command Enforcement
All components ───────────→ Append-only Audit Stream → SIEM / Data Lake
```

### 强制架构约束

- 控制面与数据面分离：注册表保存元数据，业务数据仍留在源系统或受控运行环境。
- 每个数据面请求都由短期身份、策略决策和可追踪请求 ID 约束。
- 审批只是补充控制，不能替代沙箱、网络策略或源系统 ACL。
- 单租户/业务单元之间通过组织、项目、环境、数据域和服务账号五层边界隔离。

## MVP 功能范围（第一阶段）

1. **Skill Registry**：登记卡、版本、构件哈希、SBOM、所有者、风险等级、有效期、停用开关。
2. **PR Gate**：schema 校验、Gitleaks、依赖扫描、SBOM、许可检查、脚本静态分析、行为/Eval 测试。
3. **RBAC 与审批**：基于企业 SSO/SCIM 组的分发授权；L3 运行审批与双人校验。
4. **运行时策略**：只读/工作区写入、网络关闭/白名单、工具/命令允许列表、环境隔离。
5. **Connector / MCP Gateway**：统一令牌交换、动作分级、参数校验、速率限制、审计和撤销。
6. **审计与应急**：不可篡改事件流、SIEM 检索、按 Skill/版本/身份的 kill switch。

## 推荐工具栈（可替换）

| 能力 | 推荐选择 | 选择标准 |
| --- | --- | --- |
| 源码与评审 | GitHub Enterprise / GitLab | 强制 PR、CODEOWNERS、分支保护、审计 |
| CI 门禁 | GitHub Actions / GitLab CI | 仅受信 runner；构建隔离；产物可追溯 |
| Secret 检测 | Gitleaks | PR 与历史扫描；阻断泄漏 |
| SCA / SBOM | Trivy + Syft 或企业 SCA | CVE、许可、CycloneDX/SPDX 输出 |
| 签名与溯源 | Sigstore Cosign + SLSA provenance | 验证发布者与不可变构件 |
| 身份与组 | Okta / Entra ID + SCIM | SSO、自动离职回收、组级授权 |
| 策略引擎 | Open Policy Agent (OPA) | 集中、可测试的 allow/deny 决策 |
| 密钥 | HashiCorp Vault 或云 KMS/Secret Manager | 短期凭据、轮换、审计、逐项吊销 |
| 沙箱 | Kubernetes namespace + gVisor/Kata；或受控云容器 | 隔离、只读根文件系统、资源与网络限制 |
| 出站网络 | egress proxy + DNS/域名 allowlist | 记录并阻断非批准访问 |
| MCP 入口 | 内部 MCP Gateway / API Gateway | 身份透传、schema 校验、动作审批、速率限制 |
| SIEM | Splunk / Microsoft Sentinel / Elastic | 关联身份、审批、连接器与运行事件 |
| 质量/安全评测 | Promptfoo + 自建攻击集 | 提示注入、数据泄露、越权工具调用回归 |

工具不是强制绑定；关键是每项能力必须有清晰的控制所有者、接口、审计和停用机制。

## 数据模型

| 实体 | 关键字段 |
| --- | --- |
| Skill | skill_id、名称、所有者、业务单元、风险等级、状态、到期日 |
| SkillVersion | 语义版本、构件 hash、签名、SBOM、源码提交、审批状态 |
| CapabilityRequest | 目录/网络/连接器/动作/数据域、理由、授权范围、有效期 |
| PolicyBinding | subject、resource、action、environment、conditions、expiry |
| ConnectorGrant | 外部系统、身份类型、scope、动作等级、撤销状态 |
| Approval | 执行计划、审批人、双人规则、时间、到期、决策依据 |
| Execution | request_id、Skill 版本、环境、工具调用、网络、结果、风险标签 |
| SecurityEvent | 策略拒绝、异常、告警、事件等级、处置状态、证据链接 |

## API 与策略示例

```text
POST /skills/{skillId}/versions              提交构件与元数据
POST /capability-requests                    申请网络/连接器/写入权限
POST /approvals/{approvalId}/decide          批准或拒绝高风险运行
POST /skills/{skillId}/suspend               紧急停止分发和运行
GET  /audit?skill=&version=&actor=&from=     审计检索
```

策略应使用“拒绝优先”。例：`risk=L3 && action in [delete, deploy, send_external, change_iam]` 时，除非两个独立审批仍有效、服务身份匹配且环境为批准的生产环境，否则拒绝。

## 90 天实施路线

| 时间 | 交付物 | 验收标准 |
| --- | --- | --- |
| 0–30 天 | 资产盘点、风险分级、私有注册表、Git PR 门禁、SSO 组 | 100% 已知 Skill 有所有者、版本和风险等级；无明文密钥 |
| 31–60 天 | 沙箱基线、网络白名单、Vault、连接器分级、审批服务 | L2/L3 不能绕过运行时策略；审批与身份可关联 |
| 61–90 天 | MCP Gateway、SIEM 仪表盘、kill switch、季度复审和红队回归 | 15 分钟内可停用任一 Skill；审计链可用于调查 |

## 部署、回滚与运营

- 管理平台采用高可用控制面；策略以 GitOps 管理、测试和分阶段发布。
- 新策略先在 audit-only 模式观察，确认无误后执行强制拒绝；但涉及已知高危外发、删除和 IAM 修改的规则应直接强制。
- 策略、注册表和审批数据每日备份；审计流写入具备不可变保留能力的存储。
- 每个组件暴露健康、拒绝率、延迟、批准失败、密钥错误、非白名单网络请求和 kill-switch 状态。
- 控制面不可用时，高风险写操作采取 fail-closed；低风险只读能力可按业务连续性策略降级。

## 与 Codex / ChatGPT 的实践映射

工作区 Skill、本地文件系统 Skill 和 Plugin 需要分别治理；插件中若包含连接器，还必须同时满足插件可用性、App 权限、外部服务授权和运行时权限。Codex 本地/CLI/IDE 的推荐基线是受限工作区写入和无网络；云端可以使用隔离容器、设置阶段网络和代理阶段去密钥的模型。

## 参考

- [OpenAI Skill controls](https://learn.chatgpt.com/docs/enterprise/skills)
- [OpenAI Plugin controls](https://learn.chatgpt.com/docs/enterprise/apps-and-connectors)
- [OpenAI Codex approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security)
- [OpenAI Compliance API guidance](https://learn.chatgpt.com/docs/enterprise/compliance-api)
