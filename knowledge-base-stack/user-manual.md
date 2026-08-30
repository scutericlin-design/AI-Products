# Personal Knowledge Stack User Manual

这份手册是给日常使用看的，不是部署说明。

你的系统现在分三层：

- Notion: 长期知识库和唯一事实源。
- Onyx Cloud: 全库检索、带引用问答、Agent 工作台。
- NotebookLM: 专题研究室，用来消化一组资料并产出结构化结论。

主链路：

```text
记录和沉淀 -> Notion
跨库检索 -> Onyx
专题消化 -> NotebookLM Source Pack / Mac 本机上传
结论写回 -> Notion
再次索引 -> Onyx
```

## 当前状态

已经完成：

- Notion 页面: `AI Knowledge Hub`
- Notion 数据库:
  - `Research Packs`
  - `Source Library`
  - `Evergreen Notes`
  - `Decisions`
  - `Projects`
  - `AI Outputs`
  - `SOP / Templates`
- Onyx Cloud:
  - Notion connector 已配置并完成索引。
  - Document Set: `AI Knowledge Hub`
  - Agents:
    - `Personal Knowledge Recall`
    - `Product Research Synthesizer`
    - `Project Navigator`
    - `Weekly Knowledge Curator`
- NotebookLM:
  - Hermes 已切换为 `KNOWLEDGE_STUDY_WORKSPACE=notebooklm`
  - Source Pack 云端输出目录：`knowledge-base-stack/notebooklm-source-packs/`
  - Google / NotebookLM 上传动作放到你的 Mac 本机执行，依赖本机 VPN 和 Google 登录态。

当前限制：

- Onyx 的 SiliconFlow LLM provider 现在返回 `Sorry, your account balance is insufficient`。在修复 SiliconFlow 余额/资格或切换模型供应商前，Onyx 检索资料可以存在，但聊天问答可能不可用。
- NotebookLM 没有稳定公开的服务端 source 创建 API，所以 Hermes 负责 24 小时自动生成 Source Pack、更新 Notion、触发 Onyx、返回下载链接；上传 NotebookLM 由本机同步器在你的 Mac 上处理。

## 今天怎么开始

只做三件事，不要一上来整理整个知识库。

1. 打开 Notion 的 `AI Knowledge Hub`。
2. 选一个你现在真的关心的问题，建一条 `Research Pack`。
3. 往这个 Research Pack 里放 3-10 条资料，然后让我同步成 NotebookLM Source Pack。

一个好的第一个 Research Pack 例子：

```text
Name: 2026-06 个人 AI Agent 工作流
Question: 我应该如何设计一个低维护、可复用的个人 Agent 知识工作流？
Status: Researching
Topic: AI Agent / Knowledge Base
Priority: High
AI Index: 暂时不勾选
```

刚开始不要追求字段完整。最小可用字段只有三个：

- `Name`: 研究主题。
- `Question`: 要回答的问题。
- `Status`: 当前状态。

## Notion 怎么用

Notion 是长期知识库。所有重要结论最后都应该回到 Notion。

### Research Packs

用途：一次专题研究的主记录。

什么时候新建 Research Pack：

- 一个问题需要读多篇资料才能判断。
- 你希望之后能复用这次研究。
- 这个主题可能进入 NotebookLM 深度消化。
- 这个主题以后可能被 Onyx 检索。

状态建议：

- `Inbox`: 只是一个待研究想法。
- `Researching`: 正在收集资料。
- `Synthesizing`: 已经开始总结和判断。
- `Done`: 已经形成结论或决策。
- `Archived`: 不值得继续。

使用规则：

- 一个 Research Pack 只回答一个核心问题。
- 不要把长期观点直接写在 Research Pack 里结束，重要观点要提炼成 Evergreen Notes。
- Done 之后再考虑勾选 `AI Index`。

### Source Library

用途：放原始资料、链接、文章、PDF、视频、报告。

每条资料只需要先填：

- `Name`
- `URL`
- `Type`
- `Added For`
- `Quality`

质量分级：

- `A`: 官方文档、一手数据、可信研究、原始材料。
- `B`: 有参考价值，但需要交叉验证。
- `C`: 可以启发，但不能当关键证据。
- `Unknown`: 还没判断。

### Evergreen Notes

用途：沉淀长期有效的知识。

好的 Evergreen Note 标题应该是一个观点，而不是一个宽泛主题。

好例子：

```text
NotebookLM 应该作为专题研究室，而不是长期知识库
Onyx 更适合做跨源检索层，而不是笔记编辑器
个人知识库的核心不是收藏，而是复用
```

差例子：

```text
NotebookLM
知识库
AI Agent
```

### Decisions

用途：记录已经做出的判断，避免反复纠结。

每次 Research Pack 结束，如果形成了选择，就写一条 Decision。

最小字段：

- `Name`
- `Decision`
- `Rationale`
- `Alternatives`
- `Revisit Date`

### Projects

用途：把知识变成行动。

当一个 Research Pack 导致实际行动，例如做产品、写文章、搭系统，就关联到 Projects。

## Onyx 怎么用

Onyx 是检索和问答层，不是笔记仓库。

适合问 Onyx：

```text
我之前关于 NotebookLM 的定位是什么？
我现在有哪些 Active Projects 和相关决策？
帮我找出关于个人知识库架构的核心结论，并给出来源。
把 AI Knowledge Hub 里与 Onyx 相关的资料按主题分组。
```

不适合问 Onyx：

```text
随便帮我想想。
直接替我读整个互联网。
把所有 Notion 页面都总结一遍。
```

Agent 使用建议：

- `Personal Knowledge Recall`: 找旧笔记、找结论、问“我之前怎么想的”。
- `Product Research Synthesizer`: 做产品、技术方案、竞品、架构类研究。
- `Project Navigator`: 查项目现状、下一步、相关决策。
- `Weekly Knowledge Curator`: 每周复盘和整理。

注意：当前 Onyx 的 LLM provider 需要先修复 SiliconFlow 余额/资格，或切到另一个可用模型供应商。这个问题修复前，先把 Notion + Hermes Source Pack + NotebookLM 作为主工作流。

## NotebookLM 怎么用

NotebookLM 是专题研究室。它不应该替代 Notion。

什么时候用 NotebookLM：

- 你有一个明确问题。
- 你已经有 5-30 条资料。
- 你想让它生成摘要、证据表、FAQ、思维导图、学习材料。
- 你准备把最后结论写回 Notion。

什么时候不要用 NotebookLM：

- 只是临时记一个想法。
- 资料还没有筛选。
- 主题太宽，问题不明确。
- 你只是想做全库搜索，这应该交给 Onyx。

推荐流程：

1. 在 Notion 创建 Research Pack。
2. 在 Source Library 收集资料，并关联到 Research Pack。
3. 让我导出 Research Pack 为 NotebookLM Source Pack。
4. Mac 本机同步器下载 `.md/.txt` 文件。
5. Mac 本机上传器把 `.txt` 内容作为 Copied text 来源导入 NotebookLM。
6. 在 NotebookLM 里生成:
   - Source Map
   - Evidence Table
   - Key Takeaways
   - Decision Memo
   - FAQ 或学习材料
7. 把有价值输出写回 Notion:
   - Research Pack 的 `Key Takeaways`
   - Evergreen Notes
   - Decisions
   - Projects

可以直接问 NotebookLM 的 prompt：

```text
请基于所有来源，生成一个 Source Map：每个来源的核心观点、可信度、适合支持哪些结论。
```

```text
请生成 Evidence Table，列出本专题最重要的 10 个 claim，每个 claim 对应证据、来源和 confidence。
```

```text
请指出这些来源之间的矛盾、不确定性和需要进一步验证的地方。
```

```text
请生成一份 Decision Memo：背景、选项、证据、推荐选择、风险、下一步行动。
```

## 自动化怎么用

你不需要自己记命令。日常可以直接对我说：

```text
把这个 Research Pack 同步到 NotebookLM。
```

或者：

```text
把 2026-06 xxx 这个专题导出成 NotebookLM Source Pack。
```

在飞书里给已有专题追加新内容：

```text
kb add 2026-06 xxx
这里写新资料、新观察、新结论或一个链接。
可以多行。
```

这会把内容追加到对应 Notion Research Pack 的 `Inbox Capture` 小节，并自动重新导出 Source Pack。

我会做这些事：

1. 使用已配置的 Notion API token。
2. 如有 `kb add`，先把新内容追加到 Notion Research Pack。
3. 导出 Research Pack。
4. 生成 `.md`、`.txt`、`.json`。
5. 回写 Notion 的 Source Pack 路径和导出状态。
6. 通过飞书返回 Notion 链接、Source Pack 路径、Markdown/Text 下载链接和 NotebookLM 入口。
7. 自动把 Markdown Source Pack 归档到私有 GitHub repo，形成可回滚版本历史。
8. 你的 Mac 开机后，本机同步器下载新 Source Pack 到本地缓存目录并更新 Google Drive Doc。

如果你要自己运行，基本命令是：

```bash
python3 knowledge-base-stack/scripts/export_notion_to_notebooklm_pack.py \
  --token-file /private/tmp/notion_api_key.txt \
  --target-label NotebookLM \
  --output-dir knowledge-base-stack/notebooklm-source-packs \
  --research-pack-title "Research Pack 标题" \
  --update-notion
```

列出 Research Packs：

```bash
python3 knowledge-base-stack/scripts/export_notion_to_notebooklm_pack.py \
  --token-file /private/tmp/notion_api_key.txt \
  --list
```

导入 NotebookLM 后回写状态的命令：

```bash
python3 knowledge-base-stack/scripts/update_notion_research_pack.py \
  --token-file /private/tmp/notion_api_key.txt \
  --database-id 38c35c1d-9fdc-8196-a44a-d26ed503bcfa \
  --title "Research Pack 标题" \
  --notebooklm-url "NotebookLM URL" \
  --notebooklm-sync-status Added \
  --notebooklm-source-pack-path "生成的 txt 路径" \
  --notebooklm-last-exported
```

## 每日流程

每天 5 分钟，只捕捉，不整理。

动作：

1. 新想法放进 Research Packs，状态先设为 `Inbox`。
2. 外部资料放进 Source Library。
3. 如果资料已经属于某个主题，关联到对应 Research Pack。
4. 如果一个观点长期有用，才提炼成 Evergreen Note。
5. 不要当天强行整理所有东西。

每日结束时只问自己：

```text
今天有没有一个问题值得进入 Research Pack？
今天有没有一条资料值得进入 Source Library？
今天有没有一个结论值得进入 Evergreen Notes？
```

## 每周流程

每周 30 分钟，让知识库变得更可复用。

固定问题：

1. 本周哪些 Research Packs 应该变成 `Done`？
2. 哪些 Inbox 主题应该归档？
3. 哪些 Research Packs 需要丢给 NotebookLM？
4. 哪些结论应该提炼成 Evergreen Notes？
5. 哪些结论已经变成 Decisions？
6. 哪些内容应该勾选 `AI Index`？
7. 下周最重要的 1-3 个研究问题是什么？

Onyx 可用后，可以让 `Weekly Knowledge Curator` 帮你做这一轮。

## AI Index 规则

不要把所有内容都给 Onyx。

建议勾选 `AI Index`：

- Done 状态的 Research Pack。
- Stable 状态的 Evergreen Note。
- 重要 Decision。
- Active Project 的项目说明和阶段复盘。

建议不勾选：

- Inbox。
- 原始摘录。
- 没读完的网页。
- 临时想法。
- 包含账号、财务、隐私、敏感信息的页面。

## 常见问题

### Onyx 聊天报余额不足

原因：当前 SiliconFlow provider 返回余额不足。

处理：

- 检查 SiliconFlow 账户余额和实名/资格状态。
- 充值或换一个可用 provider。
- 修好前先用 Notion + Hermes Source Pack + NotebookLM 主流程。

### NotebookLM 里来源名称不清楚

处理：

- 在 NotebookLM 来源列表中找到对应来源。
- 选择重命名来源。
- 改成 `NotebookLM Source Pack - 主题名`。

### 导出脚本找不到 Research Pack

通常是标题不完全一致。

先列出 Research Packs：

```bash
python3 knowledge-base-stack/scripts/export_notion_to_notebooklm_pack.py \
  --token-file /private/tmp/notion_api_key.txt \
  --list
```

然后复制准确标题再导出。

### Notion API 看不到页面

通常是 Notion connection 没有访问对应页面。

处理：

- 打开 Notion developer connection。
- 在内容访问权限里添加 `AI Knowledge Hub`。
- 不要授权整个 workspace。

### 一个专题有多个 Source Pack

这是正常的。每次资料变化较大时，生成一个新版本。

规则：

- NotebookLM 里保留能解释研究过程的版本。
- Notion 的 Source Pack Path 指向最新版本。
- 旧版本可以保留，不要静默覆盖。

## 你的第一周目标

第一周不要追求自动化很多。

目标只有四个：

1. 创建 3 个真正有用的 Research Packs。
2. 每个 Research Pack 至少关联 3 条 Source Library 资料。
3. 选择其中 1 个同步到 NotebookLM 做深度研究。
4. 从这个专题里提炼 3 条 Evergreen Notes 和 1 条 Decision。

完成这四个动作后，这套系统就开始活起来了。
