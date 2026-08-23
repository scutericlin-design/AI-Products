# 企业 SDD Agent 开发与 SDLC 自动化审查手册

> 版本：v1.0 · SDD = Spec-Driven Development · 适用范围：需求到发布的 Agent 辅助软件交付、代码/测试/基础设施自动化与变更治理

## 1. 核心结论

SDD Agent 的企业 Review 不应只审代码 diff，而应审查完整的**变更合同**：规格、设计、威胁模型、任务计划、实现、测试、工具轨迹、发布证据和线上结果。

**规格是源头、测试是可执行证据、人工是高风险责任主体。** 实现 Agent 可以生成代码、测试和 PR；不得在未获批准时自行改写规格、自行弱化验收测试或自行批准生产发布。

```text
需求 → G1 Spec Ready → G2 设计/计划 → G3 实现与验证 → G4 独立 Review → G5 发布/线上验证
             │                 │                  │                    │                 │
        产品/架构批准       安全/Owner 批准      CI 自动门禁         人工 PR 审批        变更审批
```

## 2. SDD 变更合同

每一项功能、修复或基础设施变更均以唯一 `change_id` 建档；所有产物以同一个 ID 双向关联。

```text
change_id → Spec / AC → Design / ADR → Tasks → PR / Diff → Tests → CI Evidence → Release → Telemetry
```

推荐结构：

```text
specs/ORD-142/
  requirements.md        # 目标、范围、非目标、验收条件
  design.md              # 架构、接口、数据、ADR
  threat-model.md        # 资产、信任边界、滥用路径与缓解
  tasks.yaml             # 可执行任务、依赖、Owner、Agent 边界
  acceptance.yaml        # 可机器/人工验证的验收条件
  traceability.json      # AC → task → PR → test → release
  review/                # 各阶段意见、例外与发布证据
```

### 2.1 必填的规格字段

```yaml
change_id: ORD-142
owner: commerce-platform@example.com
risk: L1                         # L0 / L1 / L2 / L3
goal: 将订单取消请求异步化
non_goals: [不改变退款规则, 不迁移历史订单]
acceptance_criteria:
  - id: AC-01
    given: 已确认订单
    when: 用户发起取消
    then: 10 分钟内产生可审计取消事件
  - id: AC-02
    given: 无取消权限的用户
    when: 调用取消接口
    then: 返回 403 且不产生状态改变
security: [least-privilege, audit-log]
performance_budget: p95 < 800ms
rollback: feature_flag:async_cancel=false
approval: [product-owner, tech-lead]
```

规格必须写出非目标、失败路径、数据分类、权限、性能/SLO、迁移、回滚和验收条件。缺少任一项时，Agent 只能提出澄清问题，不能进入实现。

## 3. 角色与权限分离

| 角色 | 允许动作 | 禁止动作 |
| --- | --- | --- |
| Spec Agent | 把需求整理为规格草案，提出缺口 | 批准规格或改写已批准目标 |
| Plan Agent | 将批准 Spec 分解为任务、测试和依赖 | 扩大范围、绕过架构边界 |
| Implement Agent | 在隔离分支/沙箱中编写代码和测试 | 直接合并、发布、修改受保护 Golden Test |
| Test Agent | 补充测试、运行验证、报告证据 | 仅靠 Mock 宣称真实集成已验证 |
| Review Agent | 按 Spec/ADR/Rubric 审查计划、diff、测试 | 审批自身或实施 Agent 的例外 |
| Security Agent | 审查 Secrets、依赖、IaC、权限和攻击面 | 写入生产、授予更高权限 |
| Release Agent | 准备发布包、监控灰度、提出回滚建议 | 绕过人工生产审批 |
| Human Owner | 批准规格、例外、合并和高风险发布 | 无证据口头放行 |

生产与审查 Agent 应采用独立上下文、独立 Prompt、独立工作目录和最小化工具权限。对 L2/L3，优先使用不同模型或供应商降低同源偏差。

## 4. 五道质量门禁

### G1：Spec Ready（先审对不对）

由 Spec Linter、Product Reviewer、Architecture Reviewer 共同完成：

- 每个需求有唯一 ID 与可验证 `given/when/then` 验收条件；
- 目标、非目标、边界和依赖明确；
- API、数据模型、权限、错误语义、性能、可观测性、迁移与回滚已定义；
- L2/L3 已完成威胁建模与数据/合规审查；
- 规格和 ADR 都有 Owner、版本与批准记录。

**放行标准：** 无关键缺项，所有 `AC-*` 可被测试或人工确认；否则 `REWORK`。

### G2：Design & Plan Review（再审怎么做）

Plan Reviewer 检查：任务是否覆盖全部 `AC-*`；任务顺序和依赖是否合理；是否有架构越界、新外部依赖、数据迁移或不可逆操作；是否包含单测、集成、契约、端到端、故障与回滚验证。

```json
{
  "change_id": "ORD-142",
  "decision": "PASS | REWORK | ESCALATE",
  "uncovered_acceptance_criteria": ["AC-07"],
  "unsafe_assumptions": [],
  "missing_tests": ["AC-02-permission-denied"],
  "evidence": ["specs/ORD-142/requirements.md#AC-02"]
}
```

### G3：Implementation & CI（验证做得对）

CI 必须在 Agent Review 前完成并保存证据：

- 编译、格式化、静态检查、单元、集成、契约、端到端、回归测试；
- SAST、Secrets、SCA/许可证、容器、IaC 和依赖信誉扫描；
- API/Schema diff、数据库迁移与前向/回滚演练；
- 覆盖率、性能预算、错误处理、日志/指标/追踪检查；
- 检测被删减、跳过或弱化的既有验收测试。

Golden Tests、安全测试、兼容性测试和 SLO 测试应受保护：实现 Agent 可以新增，但修改或删除必须由独立 Test Owner 审批。

### G4：Independent Review（审查是否真的满足 Spec）

Review Agent 和人类 Reviewer 的问题固定为：

1. 此 diff 是否逐项满足 `AC-*`，有何可核验测试证据？
2. 是否引入虚构 API、错误依赖、无证据假设或未批准范围？
3. 是否改变授权、数据留存、隐私、失败语义、幂等性、事务或兼容性？
4. 测试是否只对当前实现过拟合，是否覆盖负例和真实集成？
5. 是否符合 ADR、代码约定、可维护性和可观测性要求？

Review finding 必须是 `severity + spec_id + diff_evidence + remediation`，不能只给“代码可读性一般”等主观结论。

### G5：Release & Production Review（上线仍需证明）

```yaml
release:
  change_id: ORD-142
  pr: 381
  ci_evidence: https://ci.example/runs/819
  security_findings: zero-or-accepted-with-owner
  migration: forward-and-rollback-tested
  feature_flag: async_cancel
  canary_scope: 5_percent
  slo: error_rate < 0.5_percent
  rollback_owner: commerce-oncall
```

L2/L3 变更必须双人批准、短期生产身份、灰度、告警和验证过的回滚方案。Agent 可提出或执行预授权、可撤销步骤，不得绕过变更流程。

## 5. PR 与发布的强制模板

```markdown
## Spec traceability
- Change / Spec: ORD-142
- Covered AC: AC-01, AC-02, AC-07
- 未覆盖 AC 与原因：

## Agent evidence
- [ ] Agent、模型、Prompt、工具与版本已记录
- [ ] 计划审查、CI、SAST、SCA、Secrets、IaC 已通过
- [ ] Golden Test 未被绕过；新增/修改测试已说明
- [ ] 迁移、性能、可观测性和回滚已验证
- [ ] Agent Review findings 已修复或已接受风险

## Human decision
- [ ] Code Owner 已批准
- [ ] 架构/数据/安全变更已按需批准
- [ ] 发布、灰度、告警和回滚 Owner 明确
```

任何自动代码审查机器人只提供意见，不能计入 required approval。GitHub 的 Copilot Code Review 也遵循这一做法：它只留下评论，不替代人类批准。

## 6. 风险分级与自动化边界

| 风险 | 示例 | 自动化边界 |
| --- | --- | --- |
| L0 | 文档、内部脚本、低风险 UI 文案 | Agent 可创建 PR；按比例人工抽检 |
| L1 | 常规业务功能、非敏感服务 | CI + Agent Review + 至少一名 Code Owner |
| L2 | API、数据库、依赖、客户数据、生产 IaC | 架构/数据/安全审批；不得自动发布 |
| L3 | 权限、支付、删除数据、密钥、监管业务 | 默认不自动执行；双人审批与强制回滚 |

## 7. SDLC Agent 的评测集与指标

评测不是只看“代码能否编译”。每个服务至少保留以下回归样本：历史缺陷、权限失败、边界输入、兼容性破坏、依赖投毒、迁移失败、故障注入和发布回滚。

| 指标 | 定义 | 用途 |
| --- | --- | --- |
| Spec 覆盖率 | 有 task/test/证据的 AC ÷ 全部 AC | 发现漏实现 |
| 需求偏离率 | 未批准范围变更 ÷ Agent 任务 | 约束自主性 |
| Golden Test 保护率 | 未绕过的受保护测试比例 | 防止“改测试求绿” |
| Agent-人工一致率 | Review 结论与专家盲审一致比例 | 校准 Reviewer |
| 严重问题漏检率 | 人工发现但 Agent 未升级的问题比例 | 核心风险指标 |
| 变更失败率/回滚率 | 发布后失败或回滚的比例 | 验证真实价值 |
| MTTR 与交付周期 | 从变更到恢复/发布的时间 | 平衡效率与安全 |

审查 Agent 同样纳入评测。以人工盲审样本校准其 Rubric；当严重漏检、人工推翻率或线上回滚率超阈值时，自动降级为“仅建议、必须人工审查”。

## 8. 平台与工具落地

```text
Git + Spec Registry + Prompt/ADR Registry
       ↓
Sandboxed Agent Runner → CI/CD → Policy-as-Code / DLP / Secrets / SAST / SCA
       ↓                                  ↓
Trace & Evidence Store ← Review Orchestrator ← Human Approval Queue
       ↓
Release Controller → Feature Flags / Canary / Observability / Rollback
```

最小组合：GitHub/GitLab、CI、Code Owners、分支保护、Artifact/测试报告、OpenTelemetry、策略引擎、密钥管理、特性开关与告警。可在此基础上接入 GitHub Copilot Review、Microsoft Foundry Agent Evaluation、LangSmith/Langfuse/Phoenix 等观察与评测能力。

所有 Agent 均运行在受控沙箱中：工作区写权限、网络白名单、只读生产数据、短期凭据和命令/工具 allowlist。沙箱是减少审批疲劳的边界，不是取消审查的理由。

## 9. 90 天实施路线

| 阶段 | 时间 | 重点交付物 | 退出标准 |
| --- | --- | --- | --- |
| 基线 | 0–15 天 | 一个服务、Spec 模板、PR 模板、Code Owners、风险表 | Owner 签署首批 10 个 AC |
| 可验证 | 16–30 天 | CI 基线、Golden Tests、证据归档、G1/G3 | 每个 PR 可追溯至 Spec |
| 独立审查 | 31–60 天 | Plan/Code/Test Reviewer、Review Rubric、人工盲审集 | 首轮一致率和漏检率基线 |
| 受控发布 | 61–75 天 | G5、灰度、回滚、生产 trace 与告警 | L1 变更可受控上线 |
| 规模化 | 76–90 天 | 月度治理、例外流程、第二服务、指标看板 | 团队批准扩展范围 |

## 10. 上线检查表

- [ ] Spec、ADR、任务、测试、PR、发布均以 `change_id` 可追溯。
- [ ] 每个 `AC-*` 有验证方法和证据；非目标与禁止动作已声明。
- [ ] 实现、测试、审查和发布 Agent 的身份、权限与上下文相互隔离。
- [ ] Agent 只能在分支/沙箱中写入，不能直接合并或发布。
- [ ] Golden Tests、依赖、迁移、Secrets、IaC 与许可证检查不能被实现 Agent 绕过。
- [ ] L2/L3 已具备人工审批、短期授权、灰度、告警和回滚。
- [ ] Reviewer 对照人工盲审持续校准；失败样本进入回归集。

## 11. 参考资料

- [AWS Agentic AI Lens：Specification-driven tasks](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsus01-bp05.html)：将规格、成功条件、预算和终止条件作为 Agent 调用合同。
- [GitHub：Review AI-generated code](https://docs.github.com/en/enterprise-cloud%40latest/copilot/tutorials/review-ai-generated-code)：功能检查、上下文与意图、依赖、AI 特有风险和人工监督。
- [GitHub：Copilot Code Review](https://docs.github.com/en/enterprise-cloud%40latest/copilot/how-tos/copilot-on-github/use-copilot-agents/copilot-code-review)：自动审查只评论，不计入 required approval；可引用仓库自定义指令。
- [GitHub：将 Agentic AI 接入企业 SDLC](https://docs.github.com/en/enterprise-cloud%40latest/copilot/tutorials/rolling-out-github-copilot-at-scale/enabling-developers/integrating-agentic-ai)：在人工 Review 前增加自定义合规审查 Agent。
- [Anthropic：Claude Code Best Practices](https://www.anthropic.com/engineering/claude-code-best-practices)：测试先行、独立 Agent 验证与受控权限。
- [Microsoft Agent Framework Evaluation](https://learn.microsoft.com/en-us/agent-framework/agents/evaluation)：评测任务完成、任务遵循及工具调用正确性。
