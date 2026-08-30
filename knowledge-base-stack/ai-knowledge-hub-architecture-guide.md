# AI Knowledge Hub 架构设计与使用指南

更新时间：2026-06-28

## 1. 目标

AI Knowledge Hub 是一套个人知识库与研究自动化系统。它的目标不是简单保存聊天记录，而是把值得长期复用的研究主题沉淀成结构化资料，并让 Notion、Google Drive、Onyx、NotebookLM 和 Hermes Agent 形成一条可持续运行的知识链路。

这套系统解决四个问题：

- 把临时问题变成可追踪的 Research Pack。
- 把文章、课程、网页、AI 输出归档到正确位置。
- 让长期知识进入 Notion / Onyx，方便后续检索和问答。
- 把研究资料自动生成 Source Pack，并同步到 Google Drive 云盘。

## 2. 总体架构

```text
用户在飞书发送 kb 命令
  -> Hermes / Feishu Gateway 接收消息
  -> 腾讯云 Knowledge API 创建 KnowledgeJob
  -> LLM 生成 Research Pack JSON
  -> Notion AI Knowledge Hub 创建或更新结构化页面
  -> Hermes 导出 Markdown / Text Source Pack
  -> 腾讯云保存 Source Pack，并提供签名下载链接
  -> Mac 本机同步器每 5 分钟拉取新任务
  -> Mac 使用 Google OAuth 创建或更新 Google Drive Doc
  -> Onyx 通过 Notion connector 索引长期知识
  -> NotebookLM 如需使用，由用户手动添加对应 Google Drive Doc
```

当前原则：云端负责研究、归档、缓存和通知；Google Drive 写入放在 Mac 本机执行；NotebookLM 不自动创建、不自动上传。

## 3. 核心组件

### Hermes Agent

Hermes 是 24 小时运行的云端 Agent 服务，部署在腾讯云。它负责接收飞书和微信等消息入口，并把明确的知识库命令转交给 Knowledge API。

默认知识库入口是飞书命令：

```text
kb research <主题>
kb sync <Research Pack 标题>
kb status <任务号>
kb help
```

### Feishu Gateway

飞书是当前最适合触发 research 工作流的入口。用户在飞书里发送 `kb research <主题>` 后，系统会把它识别为知识库任务，而不是普通聊天。

如果只是说“帮我研究某个课程”，Hermes 可能会按普通对话处理，不一定会生成 Research Pack，也不会同步到 Google Drive。

### KnowledgeJob

KnowledgeJob 是云端任务记录。它记录任务主题、状态、Notion 页面、Source Pack、Drive 同步信息和日志。

任务状态通常包括：

- running：正在研究或归档。
- completed：已经生成 Notion 页面和 Source Pack。
- failed：任务失败，需要看日志或重新触发。

### Notion AI Knowledge Hub

Notion 是长期知识的主库。当前结构包括：

- Research Packs：主题级研究包。
- Source Library：外部来源、链接、论文、课程、网页。
- Evergreen Notes：长期有效的原则、方法、结论。
- Decisions：已经做出的选择和理由。
- Projects：可执行项目和跟进事项。
- AI Outputs：AI、NotebookLM、Hermes 产出的摘要或分析。
- SOP / Templates：流程、规则、模板和系统说明。

本架构文档归档在 SOP / Templates，因为它属于系统运行手册。

### Source Pack

Source Pack 是给 NotebookLM、Google Drive 和后续研究工具使用的资料包。它通常包含 Markdown 和 Text 两种格式。

Source Pack 的命名通常类似：

```text
NotebookLM Source Pack - <主题>
```

注意：Google Drive 里不会出现名为 `Research Pack` 的文件。Research Pack 是 Notion 里的结构化页面；Google Drive 里同步的是 Source Pack 版本的 Google Doc。

### Google Drive Bridge

Google Drive Bridge 是本机同步层。由于腾讯云访问 Google API 不稳定，系统不让腾讯云直接写 Google Drive，而是让 Mac 本机执行 Drive 同步。

本机同步器负责：

- 每 5 分钟检查腾讯云是否有 completed KnowledgeJob。
- 下载 Source Pack 到本机缓存目录。
- 使用 Google OAuth token 创建或更新 Google Docs。
- 把任务标记为 `drive_synced`。

当前 Google Drive 文件夹：

```text
Hermes NotebookLM Source Packs
```

### Onyx

Onyx 通过 Notion connector 索引 AI Knowledge Hub。它负责长期知识检索和问答，不直接依赖 Google Drive。

适合进入 Onyx 的内容应该是长期有用、结构清晰、没有敏感隐私的内容。

### NotebookLM

NotebookLM 当前不自动创建专题，也不自动上传资料。原因是 NotebookLM 没有稳定公开的 source 创建 API，网页自动化容易受界面变化影响。

推荐做法：

- 系统自动把 Source Pack 同步成 Google Drive Doc。
- 如果某个主题需要 NotebookLM 深读，用户手动在 NotebookLM 里添加对应 Google Drive Doc。
- 后续同一个 Drive Doc 更新后，NotebookLM 可以继续读取该来源。

## 4. 运行原理

### 研究任务触发

用户在飞书发送：

```text
kb research HuggingFace AI Agents Course
```

Hermes 会创建一个 KnowledgeJob，并调用 LLM 生成结构化研究包。生成内容会写入 Notion，并导出 Source Pack。

### Notion 归档

系统会根据内容类型决定写入位置：

- 主题级研究：Research Packs。
- 外部链接、课程、论文：Source Library。
- 可长期复用的结论：Evergreen Notes。
- 明确选择和取舍：Decisions。
- 可执行事项：Projects。
- AI 产出摘要：AI Outputs。
- 流程和模板：SOP / Templates。

### Drive 同步

云端完成后，Mac 本机 LaunchAgent 会自动执行同步：

```text
~/Library/LaunchAgents/com.hermes.notebooklm-local-sync.plist
```

它会从腾讯云拉取 Source Pack，并写入 Google Drive 文件夹。这个步骤依赖 Mac 在线、网络可访问 Google、OAuth token 有效。

### NotebookLM 边界

NotebookLM 不在自动链路里。Drive Doc 已经准备好后，如果需要 NotebookLM，可以人工添加该 Doc 作为来源。

## 5. 落地步骤

### 第一步：云端 Hermes

- 腾讯云运行 Hermes Agent 和 Knowledge API。
- 飞书消息回调指向 Hermes knowledge endpoint。
- 云端配置 Notion token、Knowledge secret、Source Pack 目录。
- 云端不直接写 Google Drive。

### 第二步：Notion Hub

- 创建 AI Knowledge Hub 顶层页面。
- 建立七个核心数据库。
- 把页面授权给 Notion integration。
- 确认 Research Packs 和 SOP / Templates 可被 API 读写。

### 第三步：飞书命令

- 飞书机器人接入 Hermes。
- 使用 `kb research <主题>` 触发研究任务。
- 使用 `kb status <任务号>` 查询状态。

### 第四步：Google Drive 同步

- Google OAuth 已授权到本机。
- 本机 LaunchAgent 开机运行。
- 本机每 5 分钟检查云端任务。
- 新 Source Pack 自动同步为 Google Drive Doc。

### 第五步：检索与深读

- Onyx 负责检索 Notion 中的长期知识。
- NotebookLM 只在需要深读某个主题时手动使用。

## 6. 使用指南

### 研究一个课程

在飞书发送：

```text
kb research HuggingFace AI Agents Course
```

完成后你应该看到：

- Notion 里出现 Research Pack。
- Google Drive 文件夹里出现 `NotebookLM Source Pack - HuggingFace AI Agents Course`。
- 后续可以手动把这个 Google Doc 添加到 NotebookLM。

### 研究一个网页或文章

推荐格式：

```text
kb research <文章标题或网页主题>，重点整理核心观点、方法论、适用场景和我可以采取的行动
```

如果有 URL，直接附上 URL。

### 查询任务状态

```text
kb status <任务号>
```

如果忘记任务号，可以先看飞书完成回执，或者让系统查询最近任务。

### 重新导出已有专题

```text
kb sync <Research Pack 标题>
```

适用于 Notion 页面已经更新，需要重新生成 Source Pack 并同步到 Drive 的情况。

### 在 Google Drive 查找文件

进入文件夹：

```text
Hermes NotebookLM Source Packs
```

搜索：

```text
NotebookLM Source Pack
```

或按主题搜索，例如：

```text
HuggingFace AI Agents Course
```

## 7. 内容归档规则

默认规则：

- 长期有用的内容才进入 AI Index。
- 临时聊天、敏感信息、不成熟想法不默认进入 AI Index。
- 主题研究进入 Research Packs。
- 外部来源进入 Source Library。
- 抽象原则进入 Evergreen Notes。
- 操作流程进入 SOP / Templates。
- AI 生成总结进入 AI Outputs。

Agent 可以根据 AI Knowledge Hub 的规则自动归档，不需要每篇文章都人工 approve。

## 8. 运维与排障

### Google Drive 里找不到文件

先确认是否真的使用了 `kb research`。普通聊天不会生成 KnowledgeJob。

再检查：

- Notion 是否生成了 Research Pack。
- 云端 KnowledgeJob 是否 completed。
- Mac 是否在线。
- VPN 或 Google 网络是否可用。
- 本机同步器是否运行。

### 本机同步器状态

检查 LaunchAgent：

```text
launchctl print gui/$(id -u)/com.hermes.notebooklm-local-sync
```

日志位置：

```text
~/Library/Logs/Hermes/notebooklm-local-sync.out.log
~/Library/Logs/Hermes/notebooklm-local-sync.err.log
```

### NotebookLM 没有出现新专题

这是正常的。当前设计是不自动创建 NotebookLM 专题，也不自动上传。NotebookLM 需要时人工打开，并添加 Google Drive Doc 来源。

### HuggingFace 或其他主题没有同步

如果 Google Drive 里没有，优先检查该主题是否出现在 KnowledgeJob 列表里。没有 KnowledgeJob 就说明没有进入 `kb research` 流程。

正确命令：

```text
kb research HuggingFace AI Agents Course
```

## 9. 当前边界

稳定自动化：

- 飞书命令触发。
- Hermes 云端任务。
- LLM 研究整理。
- Notion 归档。
- Source Pack 导出。
- Mac 自动同步 Google Drive Doc。
- Onyx 通过 Notion connector 检索。

非自动化：

- NotebookLM 自动创建专题。
- NotebookLM 自动上传来源。
- 腾讯云直接调用 Google API。

未来可优化：

- 把 Drive Bridge 搬到 Google Cloud Run 或 Apps Script，让它不依赖 Mac 在线。
- 增加 `kb recent` 查询最近任务。
- 增加 `kb drive <主题>` 返回 Google Drive Doc 链接。
- 增加失败任务自动重试和飞书告警。

## 10. 最小使用口诀

要沉淀到知识库和 Google Drive，用：

```text
kb research <主题>
```

要重新导出已有 Notion 专题，用：

```text
kb sync <Research Pack 标题>
```

要查状态，用：

```text
kb status <任务号>
```

要在 Drive 里找资料，搜索：

```text
NotebookLM Source Pack - <主题>
```
