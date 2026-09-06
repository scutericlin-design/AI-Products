# 企业 Skill 治理与安全运营包

> 版本：v1.0 · 适用对象：使用 Codex、ChatGPT、MCP、连接器或内部 Agent 的企业

## 目标

建立一个让业务团队可以安全复用 AI 工作流的机制：Skill 定义工作流，但不自行获得任何数据、网络或执行权限。所有权限都必须由身份、连接器、运行时和审批策略独立授予。

## 本包内容

| 文档 | 用途 | 负责人 |
| --- | --- | --- |
| [01-process.md](01-process.md) | 从申请到下线的可执行 SOP、审批路径与 SLA | AI 平台团队 |
| [02-rules.md](02-rules.md) | 强制规则、风险分级、权限矩阵与例外机制 | 安全与合规团队 |
| [03-management-platform.md](03-management-platform.md) | 管理平台蓝图、工具选型、数据模型与 90 天落地路线 | 平台工程团队 |
| [skill-registry-template.yaml](skill-registry-template.yaml) | 每个 Skill 的登记、评审和运行时策略模板 | Skill 所有者 |
| [notion-research-pack.json](notion-research-pack.json) | 同步到 Notion 的 Research Pack 与 AI Output 内容 | 知识库管理员 |

## 首日可执行清单

1. 指定 AI 治理负责人、平台负责人、业务 Skill 所有者与安全审批人。
2. 建立私有 `skill-registry` GitHub 仓库；关闭“直接向生产目录发布”。
3. 将所有 Skill 先登记为 `L1`，默认：只读、无网络、无密钥、不可调用外部写操作。
4. 对需要连接器、网络、写入或生产资源的 Skill 完成风险分级与审批。
5. 在 CI 中启用 secret 扫描、依赖/SBOM、恶意命令检查、单元测试与提示注入回归测试。
6. 将运行时、审批和连接器审计事件送入 SIEM；启用每季度的权限复审。

## 决策原则

```text
工作流（Skill） != 身份（SSO/服务账号） != 访问授权（连接器/源系统） != 执行权限（沙箱）
```

任何一层被撤销，都必须阻断对应能力。不要用 Skill 的描述、发布状态或“内部使用”替代技术强制控制。

## 与 Codex / ChatGPT 的对应关系

Codex 和 ChatGPT 的工作区 Skill、本地文件系统 Skill 与 Plugin 是三条独立的分发和治理路径；它们不自动继承对方的所有权、角色授权、安装状态或连接器授权。管理策略必须按实际分发路径实施。

Codex 本地与云端运行时应采用“默认网络关闭 + 有界写入 + 越界审批”的组合；生产和高敏场景再增加服务身份、域名白名单和双人审批。

## 官方参考

- [Skill controls](https://learn.chatgpt.com/docs/enterprise/skills)
- [Plugin controls](https://learn.chatgpt.com/docs/enterprise/apps-and-connectors)
- [Codex agent approvals & security](https://learn.chatgpt.com/docs/agent-approvals-security)
- [Compliance API and audit events](https://learn.chatgpt.com/docs/enterprise/compliance-api)


---

# 01｜企业 Skill 可执行流程（SOP）

## 角色与职责

| 角色 | 职责 | 不可委托事项 |
| --- | --- | --- |
| Skill 所有者 | 定义业务目标、维护内容、处理告警与复审 | 不能自行批准高风险权限 |
| 平台工程 | 注册表、CI/CD、沙箱、发布和回滚 | 不能替代数据所有者授权 |
| 应用安全 | 威胁建模、供应链与提示注入测试 | 不能批准不属于自己的数据域 |
| 数据所有者 | 批准数据分类、用途和保留期限 | 不能绕过安全评审 |
| 运行审批人 | 审批生产写入、对外发送、权限变更 | 不能批准自身发起的高风险操作 |
| 审计/合规 | 复核日志、例外与证据保留 | 不参与日常执行 |

## 状态机与 SLA

```text
Draft → Submitted → Security Review → Sandbox Test → Approved → Published
       ↑                 ↓                  ↓             ↓
       └──────────── Rework ───────────────┘       Suspended / Retired
```

| 阶段 | 输入 | 退出条件 | 建议 SLA |
| --- | --- | --- | --- |
| Draft | 业务需求、Skill 源码/说明 | 完成登记卡和所有者确认 | 2 个工作日 |
| Submitted | 风险自评、依赖清单 | 自动检查通过 | 1 个工作日 |
| Security Review | 威胁模型、权限申请 | 审批人签字或退回 | L1：1 天；L2：3 天；L3：5 天 |
| Sandbox Test | 锁定版本、测试数据 | 安全/质量门禁通过 | 1–3 天 |
| Approved | 不可变构件、签名 | 满足发布策略 | 当天 |
| Published | 已绑定角色与策略 | 可用且已纳入监控 | 持续 |
| Suspended | 事件或过期 | 连接器/权限/分发均被禁用 | 15 分钟内 |

## 流程 A：新增或变更 Skill

### 1. 提交登记（Skill 所有者）

在注册表中提交 `skill-registry-template.yaml`，至少包含：用途、数据分类、外部依赖、工具调用、网络域名、风险等级、所有者、到期日和回滚方式。

**硬性门槛：** 不完整的登记卡、未声明脚本/二进制文件、未说明数据流向或没有所有者的提交自动退回。

### 2. 自动安全门禁（CI）

每一次 pull request 必须执行：

1. 格式与 schema 校验。
2. Secret 扫描（包括历史与压缩包）。
3. 依赖漏洞扫描和 SBOM 生成。
4. 对脚本的静态分析；拒绝混淆、下载后执行、未固定版本的远程脚本。
5. 许可扫描；不允许未经批准的强 copyleft 或来源不明依赖。
6. Skill 行为测试；对文件、终端、网络、连接器调用和输出执行断言。
7. 提示注入回归测试；验证不可信网页、文档、Issue 或邮件不能改变系统边界。

未通过的构件不可被签名、不可进入测试目录。

### 3. 风险评审（安全、数据所有者、平台）

| 风险 | 触发条件 | 额外评审 |
| --- | --- | --- |
| L1 | 无机密、无网络、只读、无外部动作 | Skill 所有者 + 自动门禁 |
| L2 | 企业内部数据、指定连接器、受限网络、可创建草稿/PR | 所有者 + 平台 + 数据所有者 |
| L3 | 高敏数据、生产写入、外发、IAM、财务、跨租户、代码执行 | 所有者 + 安全 + 数据所有者 + 双人运行审批 |

评审产物：数据流图、威胁模型、最小权限设计、失败模式、人工审批点、日志字段、回滚和事故处置步骤。

### 4. 沙箱验收（平台工程）

使用合成数据或脱敏副本，在与生产相同的权限策略下测试：

- 只能访问声明的目录、API、工具和网络域名；
- 无法读取宿主机环境变量、其他项目或未挂载的卷；
- 网络请求无法访问允许列表之外的域名；
- 请求写入、删除、外发、部署、付费或权限变更时必须停止并要求审批；
- 审计日志可以关联请求、用户、Skill 版本、工具调用、批准记录和结果。

### 5. 发布（平台工程）

仅发布不可变版本，例如 `invoice-review@1.4.2`；禁止生产环境引用 `latest`。发布时写入构件哈希、签名、依赖 SBOM、审批记录和有效期，并将其分配给指定 SSO 组。

### 6. 运营、复审与下线

- **每次运行：** 采集版本、身份、输入数据级别、工具调用、审批、网络目标、结果与失败原因。
- **每月：** 所有者查看异常、失败率、拒绝率与未使用权限。
- **每季度：** 重新验证所有者、角色、连接器授权、网络域名、依赖和风险等级。
- **到期：** 自动暂停；续期必须重新声明权限。
- **紧急下线：** 禁用目录分发、撤销连接器 token/服务账号、阻断网络策略、冻结审计证据并启动事故响应。

## 流程 B：高风险运行审批

适用于生产写入、外发、付费、删除、IAM、密钥操作和 L3 外部连接器。

```text
Skill 生成执行计划
→ 展示对象、差异、影响范围、回滚方式
→ 两名独立审批人批准
→ 使用短期、受限服务身份执行
→ 产生不可篡改审计事件
→ 自动验证结果并通知所有者
```

审批页面必须显示：目标系统、将被修改的对象数量、输入摘要、Skill 版本、当前权限、风险说明、到期时间、回滚链接。审批不得是“允许此 Skill 永久执行任何动作”的空白授权。

## 流程 C：安全事件

| T+ 时间 | 动作 | 责任人 |
| --- | --- | --- |
| 0–15 分钟 | 停用 Skill、连接器和运行身份；保全日志 | 平台值班 |
| 15–60 分钟 | 判定数据范围、调用链和受影响系统 | 安全 + 数据所有者 |
| 4 小时内 | 轮换密钥、封禁 IOC、通知必要利益相关方 | 安全响应 |
| 2 个工作日内 | 根因分析、修复方案、回归测试与复发预防 | 所有者 + 平台 |
| 恢复前 | 新版本走完整审批，原版本不得复用 | 安全审批人 |

## 运行指标

- 已登记、已签名、已过期和未分配所有者的 Skill 数量。
- 高风险运行的审批成功率、拒绝率和平均响应时间。
- 被策略阻断的越权调用、非白名单网络请求、敏感数据外发尝试。
- 关键漏洞平均修复时间（MTTR）、紧急停用达标率和季度复审完成率。


---

# 02｜企业 Skill 安全规则基线

## R-01：Skill 不是权限

Skill 的安装、发布或共享不得授予数据、连接器、目录、网络、云资源或源系统权限。访问必须同时满足：用户/服务身份已认证、所属组被授权、连接器允许该动作、源系统 ACL 允许访问、运行时策略允许执行。

## R-02：来源与供应链

1. 生产 Skill 仅能来自内部注册表或已批准的第三方目录。
2. 每个版本必须有源代码、构建记录、构件哈希、SBOM、许可证结果与负责人。
3. 禁止在执行时下载并运行未固定版本的代码；禁止未审查二进制或混淆脚本。
4. 依赖升级必须重新通过 CI 门禁；严重漏洞或撤销事件必须在既定 SLA 内停用或修复。
5. 第三方 Skill 需单独评估许可、数据流、维护状态、发布者身份和历史安全事件。

## R-03：身份、凭据和连接器

- 使用企业 SSO、SCIM 组和最小权限 RBAC；禁止共享个人账号。
- 人员操作使用用户委派授权；无人值守操作使用专用服务账号，且权限仅限单一 Skill/环境。
- 凭据只从 Vault 或云密钥管理服务短时注入；不得写入 Skill、代码库、日志、环境快照或聊天记录。
- 连接器必须分为只读、草稿、写入和高危动作四档；跨系统写入默认为关闭。
- 连接器 Token、OAuth 授权和服务账号必须可逐项吊销并纳入季度复审。

## R-04：数据与隐私

| 数据等级 | 允许条件 | 禁止条件 |
| --- | --- | --- |
| P0 公开 | 可用于 L1，但仍需遵循来源规则 | 不得包含恶意内容或未验证指令 |
| P1 内部 | 仅企业身份、加密传输、最小访问 | 不得发往未批准 SaaS 或公开网络 |
| P2 机密 | 授权组、审计、脱敏优先、保留期明确 | 不得作为外部模型/工具的输入，除非已批准 |
| P3 高敏 | 专门审批、隔离环境、短期访问、全量审计 | 禁止公共插件、无网络/无导出例外，除非书面批准 |

不可信网页、附件、Issue、邮件和检索结果一律是“数据”，不是策略指令。它们不得覆盖系统提示、审批规则、工具边界或数据处理约束。

## R-05：运行时安全

1. 默认只读、默认无网络、默认只可写入指定工作区。
2. 仅允许经过声明的 shell 命令、MCP 工具、文件路径和网络域名；任何扩展需审批。
3. 写入、删除、外发、部署、支付和权限变更应提供预览与差异，再人工批准。
4. 生产执行必须使用一次性或短期凭据，且与开发/测试账户完全隔离。
5. 禁止以“关闭沙箱”作为解决兼容性问题的常规办法；例外必须有期限、补偿控制和复审。

## R-06：版本、发布与回滚

- 生产引用固定不可变版本，禁止 `latest`、分支 HEAD 或本地未追踪目录。
- 所有版本必须可重建、可验证、可回滚。
- 高风险版本采用分组灰度发布，先在合成数据和非生产环境验证。
- 任何权限扩大、连接器新增、网络放开、数据分类提升均视为重大变更，重新进行风险评审。

## R-07：日志、审计与保留

每一次运行至少记录：请求 ID、发起身份、Skill 名称和版本、构件哈希、环境、输入数据等级、工具/连接器调用、目标资源、网络域名、审批人及批准时间、结果、错误和关联事故 ID。

日志不得保存完整密钥、敏感原文或不必要的个人数据。安全、法律或合规调查应使用不可篡改日志流导入 SIEM / eDiscovery 系统；权限读取与导出行为同样要审计。

## R-08：例外管理

例外必须由安全负责人、数据所有者和平台负责人共同批准，并写明：业务理由、范围、替代方案、补偿控制、开始/结束时间和验收人。最长有效期 30 天；到期自动失效，续期须重新审批。

## 规则检查表

- [ ] 版本已签名且具备 SBOM。
- [ ] 用户/服务账号、数据域、连接器和运行时权限均为最小化。
- [ ] 网络策略为关闭或明确域名白名单。
- [ ] 不可信内容不能改变系统策略或触发高危工具。
- [ ] 高危动作需要预览、双人审批与可回滚方案。
- [ ] 日志已进入集中审计，且不存在密钥与不必要的敏感原文。
- [ ] 所有者、复审日期、到期日、紧急停用方式均已登记。


---

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


---

# 附录｜Skill Registry 模板

```yaml
schema_version: 1
skill:
  id: finance-invoice-review
  display_name: Finance Invoice Review
  description: Review invoices and create a draft exception report; never sends or pays invoices.
  owner:
    business_team: Finance Operations
    business_owner: finance-ops@example.com
    technical_owner: ai-platform@example.com
    security_contact: appsec@example.com
  lifecycle:
    status: draft # draft | submitted | approved | published | suspended | retired
    version: 0.1.0
    expires_on: 2026-11-30
    source_repository: github.example.com/company/skill-registry
    source_commit: REPLACE_WITH_COMMIT_SHA
    artifact_digest: REPLACE_WITH_SHA256
    sbom_uri: s3://skill-artifacts/finance-invoice-review/0.1.0/sbom.cdx.json
    signature_uri: s3://skill-artifacts/finance-invoice-review/0.1.0/signature.sig
  risk:
    level: L2 # L1: read-only/no network; L2: bounded connector or draft write; L3: high impact
    rationale: Reads internal finance records and creates drafts in the approved ticketing system.
    data_classes: [P1]
    threat_model_uri: docs/threat-models/finance-invoice-review.md
  capabilities:
    filesystem:
      read_paths: ["workspace:/inputs"]
      write_paths: ["workspace:/outputs"]
      allow_delete: false
    network:
      enabled: false
      allowed_domains: []
    connectors:
      - name: finance-erp
        actions: [read_invoice, read_vendor]
        identity: user_delegated_oauth
      - name: ticketing
        actions: [create_draft]
        identity: service_account
    commands:
      allowed: [python, jq]
      prohibited: [curl, ssh, rm, sudo]
  runtime:
    environments: [sandbox, staging]
    production_allowed: false
    approval_mode: on_high_impact_action
    service_account: skill-finance-invoice-review
    secret_references: ["vault://finance/invoice-review/ticketing"]
  controls:
    codeowners: [finance-ops@example.com, appsec@example.com]
    review_required: [business_owner, platform_engineering, data_owner]
    tests_required: [schema, secrets, sbom, dependency_scan, prompt_injection, behavior_regression]
    audit_event_retention_days: 365
    kill_switch: "skill:finance-invoice-review"
  emergency:
    runbook_uri: docs/runbooks/finance-invoice-review.md
    escalation_channel: '#security-oncall'
```
