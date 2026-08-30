# Info Needed From User

把下面信息提供给我后，我可以继续代你执行。

## 1. Notion 自动创建

你需要先在 Notion 做两个动作：

1. 新建一个空白父页面，例如 `Codex Setup Staging`。
2. 创建一个临时 internal integration，并把这个父页面 share 给它。

临时 integration 需要的能力：

- Read content
- Insert content
- Update content

我需要你提供：

```text
NOTION_PARENT_PAGE_URL=
NOTION_API_KEY=
```

安全建议：

- 不要提供 Notion 密码。
- 这个 token 只用于本次搭建。
- 搭建完成后，你可以删除临时 integration，或从页面 connection 里移除它。
- 后续给 Onyx 用的 integration 应该另建，只给 Read content。

我会执行：

```bash
python3 knowledge-base-stack/scripts/setup_notion_knowledge_base.py \
  --parent-page-url "$NOTION_PARENT_PAGE_URL" \
  --token "$NOTION_API_KEY"
```

执行结果会创建：

- `AI Knowledge Hub`
- `Research Packs`
- `Source Library`
- `Evergreen Notes`
- `Decisions`
- `Projects`
- `AI Outputs`
- `SOP / Templates`

## 2. Onyx Cloud 配置

我需要知道：

```text
ONYX_WORKSPACE_URL=
```

你需要自己完成：

- 登录 Onyx Cloud。
- 如需付费，自己确认 plan。
- 为 Onyx 创建单独的 Notion read-only integration。

我可以继续帮你：

- 生成 Onyx connector 的配置清单。
- 生成 Document Sets。
- 给你每个 Agent 的完整 system instruction。
- 根据你的页面结构做验收问题。
- 你把 Onyx 的索引结果或截图/报错贴回来，我来排查。

如果你愿意让我通过浏览器操作，需要你确认你已经在当前机器浏览器里登录 Onyx 和 Notion。

## 3. NotebookLM 配置

我需要知道：

```text
GOOGLE_ACCOUNT_CONTEXT=个人账号 / Workspace 账号
FIRST_RESEARCH_TOPIC=
```

你需要自己完成：

- 登录 NotebookLM。
- 创建第一个 notebook 或让我根据你给的主题帮你规划。

我可以继续帮你：

- 设计第一个 notebook 的 sources 清单。
- 生成 NotebookLM prompt。
- 把 NotebookLM 输出整理回 Notion 的 Research Pack。
- 帮你判断哪些内容应该转成 Evergreen Notes。

## 4. 你的知识主题偏好

为了让初始库更像你的系统，而不是我的默认模板，请补充：

```text
TOPICS=例如 AI Agent, 产品, 交易系统, 个人管理, 写作
FIRST_3_RESEARCH_QUESTIONS=
EXISTING_NOTION_AREAS=你现在已有的 Notion 主要页面/数据库名称
SENSITIVE_AREAS=不要进入 AI Index 的内容范围
```
