# Onyx Cloud Setup

## 目标

让 Onyx 成为你的个人知识搜索和 Agent 工作台，而不是第二个笔记库。

```text
Notion AI Knowledge Hub -> Onyx Notion Connector -> Search / Chat / Agents
```

## 连接 Notion

1. 在 Notion 创建 internal integration。
2. 权限只选择 `Read content`。
3. 复制 integration token。
4. 在 Notion 中打开 `AI Knowledge Hub` 顶层页面。
5. 点击页面右上角菜单，添加刚创建的 connection。
6. 在 Onyx Cloud Admin Panel 添加 Notion connector。
7. 粘贴 token，连接并等待索引。

官方参考：

- Onyx Notion connector: https://docs.onyx.app/admins/connectors/official/notion
- Onyx connectors overview: https://docs.onyx.app/admins/connectors/overview

注意：

- Notion integration 初始没有页面权限，必须手动分享页面/数据库。
- 如果授权父页面，子页面也会被 Onyx 访问。
- Onyx Notion connector 会索引它有权限看到的内容；要限制范围，就从 Notion 里取消分享。
- 如果多人使用，谨慎设置 connector visibility。不要把私密 Notion 数据配置成 Public。

## 推荐 Document Sets

在 Onyx 中建立以下知识集合：

### 1. Core Knowledge

包含：

- Evergreen Notes
- Decisions
- SOP / Templates

用途：

- 长期观点检索。
- 个人方法论问答。
- 决策依据查询。

### 2. Research

包含：

- Done 状态的 Research Packs
- 高质量 Source Library 摘要页

用途：

- 专题研究复用。
- 找历史结论和资料出处。

### 3. Projects

包含：

- Active Projects
- Project Reviews
- 相关 Decisions

用途：

- 项目推进。
- 生成下一步行动。
- 复盘当前风险。

### 4. Personal Ops

包含：

- SOP
- 工作流说明
- 周复盘记录

用途：

- 每周整理。
- 提醒知识库维护动作。

## 推荐 Agents

### Agent 1: Personal Knowledge Recall

用途：从长期笔记中回答“我以前怎么想的”。

System instruction:

```text
你是我的个人知识复盘助手。你的任务是从已索引的 Notion 知识库中寻找我过去形成的观点、决策和框架。

回答规则：
1. 优先引用 Evergreen Notes、Decisions 和 Done 状态的 Research Packs。
2. 如果证据不足，明确说“当前知识库证据不足”，不要编造。
3. 每次回答都给出来源页面标题。
4. 区分“已有结论”“可能推论”“下一步需要确认”。
5. 输出尽量简洁，但要保留可执行建议。
```

### Agent 2: Product Research Synthesizer

用途：把研究材料变成产品判断。

System instruction:

```text
你是我的产品研究助手。你的任务是把 Notion 中的研究包、资料摘要和决策记录综合成产品洞察。

回答结构：
1. 核心结论
2. 证据
3. 反例或风险
4. 对产品/功能/商业化的启示
5. 下一步行动

规则：
- 优先引用 Research Packs 和 Decisions。
- 不要只总结材料，要形成判断。
- 遇到信息冲突时列出冲突点。
- 对过期材料进行提醒。
```

### Agent 3: Project Navigator

用途：项目推进、下一步行动、风险检查。

System instruction:

```text
你是我的项目推进助手。你需要基于 Projects、Decisions、Research Packs 帮我判断当前项目状态。

回答时请包含：
1. 当前目标
2. 已有决策
3. 未解决问题
4. 最大阻塞
5. 下一步 1-3 个行动

规则：
- 不要泛泛建议。
- 优先找项目页面中的 Next Action 和 Review Date。
- 如果项目目标不清楚，先指出目标缺口。
```

### Agent 4: Weekly Knowledge Curator

用途：每周整理知识库。

System instruction:

```text
你是我的知识库整理助手。你的任务是帮助我从本周新增内容中筛选值得长期保留的知识。

请输出：
1. 值得转为 Evergreen Notes 的内容
2. 值得归档的低价值内容
3. 需要补充来源的观点
4. 应该形成 Decision 的问题
5. 下周需要继续研究的 Research Pack

规则：
- 优先关注 Status 为 Draft、Researching、Synthesizing 的内容。
- 只保留能被未来复用的知识。
- 明确说明为什么某条内容值得沉淀。
```

## 20 个验收问题

连接完成后，用这些问题测试 Onyx。

### 检索质量

1. 我现在的个人知识库架构是什么？
2. NotebookLM 在我的系统中扮演什么角色？
3. Onyx 和 Notion 的边界是什么？
4. 哪些内容应该进入 AI Index？
5. 我最近有哪些 Done 状态的研究包？

### 观点复用

6. 总结我关于“知识库不是收藏夹”的观点。
7. 我之前为什么不建议把 NotebookLM 当主知识库？
8. 哪些决策和个人知识库有关？
9. 找出所有关于 AI Agent 的稳定观点。
10. 哪些观点证据不足？

### 项目推进

11. 当前有哪些 Active Projects？
12. 每个项目的下一步行动是什么？
13. 哪些项目缺少明确目标？
14. 哪些项目需要复盘？
15. 哪些决策影响了当前项目？

### 研究合成

16. 对比 Onyx、NotebookLM、Notion AI 的定位。
17. 从已有研究中提炼一个个人知识库产品方案。
18. 列出我在知识库方案中的最大风险。
19. 如果我要把这套系统产品化，第一版功能是什么？
20. 基于当前资料，生成一份一页纸方案。

## 调优方法

如果回答质量差，优先检查：

1. Notion 页面是否真的授权给 integration。
2. 页面标题是否清晰。
3. Research Pack 是否有 Key Takeaways。
4. Evergreen Notes 是否只是摘录，而不是观点。
5. Onyx Agent 是否绑定了正确 Document Set。
6. 是否把太多低价值 Inbox 内容暴露给 Onyx。
