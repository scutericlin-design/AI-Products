# Quick Start Checklist

## A. Notion

- [ ] 新建顶层页面 `AI Knowledge Hub`
- [ ] 在页面下创建数据库 `Research Packs`
- [ ] 导入 `templates/research-packs.csv`
- [ ] 创建数据库 `Source Library`
- [ ] 导入 `templates/source-library.csv`
- [ ] 创建数据库 `Evergreen Notes`
- [ ] 导入 `templates/evergreen-notes.csv`
- [ ] 按 `notion-blueprint.md` 调整字段类型
- [ ] 新建 `Decisions`
- [ ] 新建 `Projects`
- [ ] 新建 `SOP / Templates`
- [ ] 确认只有高质量内容勾选 `AI Index`

## B. Onyx Cloud

- [ ] 在 Notion 创建 internal integration
- [ ] integration 权限只选 `Read content`
- [ ] 把 `AI Knowledge Hub` 授权给 integration
- [ ] 在 Onyx Cloud 添加 Notion connector
- [ ] 粘贴 integration token
- [ ] 等待首次索引完成
- [ ] 创建 Document Set: `Core Knowledge`
- [ ] 创建 Document Set: `Research`
- [ ] 创建 Document Set: `Projects`
- [ ] 创建 Agent: `Personal Knowledge Recall`
- [ ] 创建 Agent: `Product Research Synthesizer`
- [ ] 创建 Agent: `Project Navigator`
- [ ] 创建 Agent: `Weekly Knowledge Curator`
- [ ] 用 `onyx-cloud-setup.md` 的 20 个问题做验收

## C. NotebookLM

- [ ] 只在需要专题研究时创建 notebook
- [ ] notebook 名称和 Research Pack 名称保持一致
- [ ] 导入 10-30 个高价值来源
- [ ] 使用 `Source Map` prompt
- [ ] 使用 `Evidence Table` prompt
- [ ] 使用 `Decision Memo` prompt
- [ ] 把结果粘回 Notion Research Pack
- [ ] 提炼 Evergreen Notes
- [ ] 把 Research Pack 状态改为 `Done`
- [ ] 勾选 `AI Index`

## D. 第一次验收

- [ ] Onyx 能找到 `NotebookLM 应该作为专题研究室，而不是长期知识库`
- [ ] Onyx 能解释 Notion / Onyx / NotebookLM 的边界
- [ ] Onyx 回答时能给出来源
- [ ] NotebookLM 输出能被粘回 Research Pack
- [ ] 至少形成 1 条 Decision
- [ ] 至少形成 3 条 Evergreen Notes

## E. 不做的事

- [ ] 不把整个 Notion workspace 暴露给 Onyx
- [ ] 不追求 NotebookLM 和 Notion 的非官方强同步
- [ ] 不把 Inbox 内容默认进入 AI Index
- [ ] 不把 NotebookLM 当长期资料库
- [ ] 不为工具链再引入一个新的主知识库
