# ima Workflow

目标：用 ima 替代 NotebookLM，作为国内优先的专题研究工作台。

## 系统定位

- Notion 是长期知识库和事实源。
- Onyx 是跨资料检索和 Agent 问答层。
- Hermes 是自动化编排层，接收飞书命令并更新 Notion。
- ima 是专题阅读和消化工作台，用来围绕一个主题 Source Pack 做问答、总结和学习材料。

## 当前自动化能力

Hermes 已支持：

1. 从飞书接收 `kb research <主题>`。
2. 生成 Notion Research Pack。
3. 导出 `ima Source Pack`，包含 Research Pack、相关 Sources、Evergreen Notes、Decisions 和 AI Outputs。
4. 通知 Onyx 走 Notion connector 自动索引。
5. 在任务回执里返回 Notion 链接、Markdown/Text 下载链接、Source Pack 路径和 ima 入口。

## 边界

ima 官方入口目前没有配置给 Hermes 使用的公开服务端导入 API。因此稳定的 24 小时云端自动化不依赖 ima UI：

```text
飞书 -> Hermes -> Notion -> Onyx / LLM -> ima Source Pack
```

把 Source Pack 导入 ima 客户端可以作为人工步骤，或后续在确认 UI 稳定后做浏览器自动化。浏览器自动化不建议作为核心生产链路，因为登录态、验证码、按钮文案和页面结构变化都会影响成功率。

2026-06-28 实测：`https://ima.qq.com/wikis` 网页端能进入知识库列表，但点击右上角上传文件后，页面提示“请前往 ima 客户端体验”，只提供“下载电脑版”。网页端没有可用的 `input[type=file]` 上传控件。因此：

- 云端 Hermes 不能通过 ima 网页端直接上传文件。
- 24 小时稳定链路仍然以 Notion + Onyx + LLM + Source Pack 下载链接为核心。
- 真正的自动上传只能等 ima 官方 API，或在你的个人电脑上运行本机客户端自动化辅助脚本。

## 飞书命令

创建专题：

```text
kb research 如何提高中学生的英语水平，并在高考中取得优异的成绩
```

重新导出已有专题：

```text
kb sync 2026-06 中学生英语提升与高考优胜策略
```

查看状态：

```text
kb status
```

## ima 中怎么用 Source Pack

1. 打开 [ima](https://ima.qq.com/)。
2. 新建或打开一个同名知识库/专题。
3. 从飞书回执点击 Markdown 下载链接，上传 Hermes 生成的 `.md` 文件；如果上传失败，点击 Text 下载链接并复制内容作为文本来源。
4. 使用 Source Pack 末尾的 Suggested ima Prompts 做消化。
5. 把最终结论回写到 Notion Research Pack 的 AI Outputs 或 Evergreen Notes。

## 推荐 Prompts

```text
请先梳理这个 source pack 中所有资料的 Source Map：每个来源的核心观点、适合回答的问题、重叠和冲突。
```

```text
请生成 Evidence Table，列出 Claim / Evidence / Source / Confidence / Caveat。
```

```text
请写一份 Decision Memo，并提炼 3-7 条适合回写 Notion 的 Evergreen Notes。
```

## 未来增强

- 如果 ima 提供公开 API：新增 `ImaApiBridge`，直接创建专题和上传 Source Pack。
- 如果需要无 API 自动导入：新增本机 ima 客户端辅助脚本，只在人工确认登录态和客户端可用时运行。
- 如果想完全云端闭环：在 Hermes 内置 `NotebookLM-lite` 研究输出，直接生成 Source Map、Evidence Table、Decision Memo、Quiz，并回写 Notion。
