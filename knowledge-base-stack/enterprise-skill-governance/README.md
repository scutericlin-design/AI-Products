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
