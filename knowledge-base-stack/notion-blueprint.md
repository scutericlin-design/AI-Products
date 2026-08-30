# Notion Blueprint

## 顶层结构

在 Notion 新建一个顶层页面：

```text
AI Knowledge Hub
- Research Packs
- Source Library
- Evergreen Notes
- Decisions
- Projects
- AI Outputs
- SOP / Templates
```

只把这个顶层页面授权给 Onyx 的 Notion integration。不要一开始授权整个 workspace。

## 1. Research Packs

用途：每一次专题研究的主记录。它是 Notion、Onyx、NotebookLM 之间的桥。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| Name | Title | 研究主题，例如 `2026-06 个人知识库架构` |
| Question | Text | 本次研究要回答的核心问题 |
| Status | Select | Inbox / Researching / Synthesizing / Done / Archived |
| Topic | Multi-select | AI Agent / Knowledge Base / Product / Trading / Writing |
| Priority | Select | High / Medium / Low |
| NotebookLM URL | URL | 对应 NotebookLM notebook 链接 |
| Onyx Agent | Select | 使用哪个 Onyx Agent 辅助 |
| Source Count | Number | 本次研究资料数量 |
| Key Takeaways | Text | 最重要的 3-7 条结论 |
| Decision | Text | 形成了什么判断或行动 |
| Next Action | Text | 下一步要做什么 |
| AI Index | Checkbox | 是否进入 Onyx 索引范围 |
| Created | Date | 创建日期 |
| Review Date | Date | 复盘日期 |

推荐视图：

- `Active Research`: Status is Researching or Synthesizing。
- `Ready for Onyx`: AI Index checked and Status is Done。
- `By Topic`: 按 Topic 分组。
- `Review This Week`: Review Date 在未来 7 天内。

## 2. Source Library

用途：管理外部资料，而不是把所有网页/PDF 都塞进长期笔记。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| Name | Title | 资料标题 |
| Type | Select | Article / PDF / Video / Podcast / Book / Report / Doc |
| URL | URL | 来源链接 |
| Author | Text | 作者/机构 |
| Published Date | Date | 发表日期 |
| Topic | Multi-select | 主题 |
| Added For | Relation | 关联 Research Packs |
| Quality | Select | A / B / C / Unknown |
| Used In Research Pack | Checkbox | 是否已经被处理过 |
| AI Index | Checkbox | 是否进入 Onyx |
| Notes | Text | 简短备注 |

规则：

- A: 可信源、原始材料、官方文档、一手数据。
- B: 有参考价值但需要交叉验证。
- C: 可启发，但不能作为关键证据。

## 3. Evergreen Notes

用途：沉淀长期有效的个人知识，不放临时摘录。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| Name | Title | 一个明确观点，而不是宽泛主题 |
| Type | Select | Principle / Framework / Insight / Playbook / Definition |
| Topic | Multi-select | 主题 |
| Status | Select | Draft / Stable / Needs Review |
| Confidence | Select | High / Medium / Low |
| Source URL | URL | 主要来源 |
| Related Research Pack | Relation | 来源研究包 |
| AI Index | Checkbox | 通常勾选 |
| Summary | Text | 3-5 句摘要 |

好标题示例：

- `NotebookLM 应该作为专题研究室，而不是长期知识库`
- `Onyx 更适合做跨源检索层，而不是笔记编辑器`
- `个人知识库的核心不是收藏，而是复用`

## 4. Decisions

用途：记录已经做出的判断，防止反复纠结。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| Name | Title | 决策标题 |
| Area | Select | Knowledge / Product / Investment / Ops / Personal |
| Decision | Text | 最终选择 |
| Rationale | Text | 为什么这样选 |
| Alternatives | Text | 放弃了哪些方案 |
| Revisit Date | Date | 何时重新评估 |
| Related Research Pack | Relation | 关联研究包 |
| AI Index | Checkbox | 勾选 |

## 5. Projects

用途：把知识转化为行动。

建议字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| Name | Title | 项目名 |
| Goal | Text | 目标 |
| Status | Select | Planned / Active / Waiting / Done / Archived |
| Related Research Packs | Relation | 关联研究 |
| Related Decisions | Relation | 关联决策 |
| Next Action | Text | 下一步 |
| Review Date | Date | 复盘时间 |
| AI Index | Checkbox | 需要被 Onyx 检索时勾选 |

## 页面模板：Research Pack

复制下面内容作为 Research Packs 的默认页面模板：

```markdown
## Research Question

本次研究要回答什么问题？

## Context

为什么现在要研究这个问题？已有判断是什么？

## Sources

- Source 1:
- Source 2:
- Source 3:

## NotebookLM Work

- NotebookLM URL:
- 生成过的内容: Summary / FAQ / Audio / Mind Map / Slides

## Evidence Table

| Claim | Evidence | Source | Confidence |
| --- | --- | --- | --- |
|  |  |  |  |

## Key Takeaways

1.
2.
3.

## Decision

我现在的判断是什么？

## Follow-up

- 下一步行动:
- 需要进入 Evergreen Notes 的观点:
- 需要进入 Projects 的事项:
```

## AI Index 规则

默认不要把所有内容都给 Onyx。

建议勾选 `AI Index` 的内容：

- Done 状态的 Research Pack。
- Stable 状态的 Evergreen Note。
- 重要 Decision。
- Active Project 的项目说明和阶段复盘。

建议不勾选：

- Inbox。
- 原始摘录。
- 未读完的网页。
- 临时想法。
- 包含隐私、账号、财务敏感信息的页面。
