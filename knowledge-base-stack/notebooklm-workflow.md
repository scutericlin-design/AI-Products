# NotebookLM Workflow

## 定位

NotebookLM 是专题研究室，不是长期知识库中枢。

适合：

- 读一组 PDF、报告、论文、网页。
- 消化 YouTube 或音频内容。
- 生成 Audio Overview、FAQ、Mind Map、学习指南。
- 围绕一个主题进行深度问答。

不适合：

- 管理长期知识。
- 做全库搜索。
- 替代 Notion。
- 替代 Onyx Agent。
- 做高频自动同步。

## 标准流程

```text
1. 在 Notion 创建 Research Pack
2. 从 Onyx / Notion / Web 选出 10-30 个来源
3. 在 NotebookLM 创建同名 notebook
4. 导入来源
5. 用 NotebookLM 做消化和问答
6. 把结论写回 Notion Research Pack
7. 提炼 1-5 条 Evergreen Notes
8. 勾选 AI Index，让 Onyx 重新索引
```

## 命名规则

NotebookLM notebook 名称和 Notion Research Pack 保持一致：

```text
YYYY-MM Topic - Core Question
```

示例：

```text
2026-06 Knowledge Stack - Notion Onyx NotebookLM
2026-07 AI Agents - Personal OS Product Direction
2026-07 Trading Research - Signal Quality Review
```

## Google Drive 中转层

如果你希望 NotebookLM 能自动吃到更新内容，优先用 Google Drive 中转：

```text
Notion 精选内容 -> 导出/整理成 Google Docs 或 PDF -> NotebookLM 导入 Drive 文件
```

适合放进 Drive 的文件：

- 研究主题说明。
- 从 Notion 导出的高质量笔记。
- PDF 报告。
- 会议纪要。
- 论文或长文。

不建议：

- 把整个 Notion 导出给 NotebookLM。
- 用非官方浏览器自动化做长期同步。
- 把敏感数据放进公开或共享 notebook。

## Source Pack 自动化

当前落地方式是把一个 Notion Research Pack 打包成单一 Source Pack，再交给 NotebookLM：

```text
Research Pack
+ related Source Library rows
+ related Evergreen Notes
+ related Decisions / Projects / AI Outputs
-> one Markdown/Text Source Pack
-> NotebookLM Copied Text source
```

使用脚本：

```bash
python3 knowledge-base-stack/scripts/export_notion_to_notebooklm_pack.py \
  --token-file /private/tmp/notion_api_key.txt \
  --research-pack-title "{Research Pack 标题}" \
  --update-notion
```

如果还没有同步字段，先运行：

```bash
python3 knowledge-base-stack/scripts/upgrade_notion_notebooklm_sync.py \
  --token-file /private/tmp/notion_api_key.txt
```

生成的 `.txt` 文件用于 NotebookLM 的 `Copied text` 导入。导入完成后，把 NotebookLM URL 回写到 Research Pack。

## NotebookLM Prompt Pack

### 1. Source Map

```text
请先不要急着总结。请帮我梳理当前 notebook 中所有来源：

1. 每个来源的核心观点
2. 该来源适合回答什么问题
3. 来源之间有哪些重叠
4. 来源之间有哪些冲突
5. 哪些来源最值得优先阅读

请用表格输出。
```

### 2. Evidence Table

```text
围绕这个研究问题：“{填入 Research Question}”

请生成一张证据表：

| Claim | Supporting Evidence | Source | Confidence | Caveat |

要求：
- 每条 Claim 必须有来源。
- Confidence 使用 High / Medium / Low。
- 如果证据不足，直接标记 Low。
```

### 3. Decision Memo

```text
基于当前 sources，请帮我写一份决策备忘录。

结构：
1. 背景
2. 可选方案
3. 每个方案的优缺点
4. 推荐方案
5. 推荐理由
6. 最大风险
7. 下一步验证动作

请明确区分事实、推论和建议。
```

### 4. Evergreen Extraction

```text
请从当前 notebook 中提炼可以长期复用的 Evergreen Notes。

每条输出：
- 标题: 一个明确观点
- 类型: Principle / Framework / Insight / Playbook / Definition
- 摘要: 3-5 句
- 证据来源
- Confidence: High / Medium / Low
- 适合放到 Notion 哪个 Topic 下

不要输出临时摘录，只输出未来能复用的观点。
```

### 5. Audio Overview Guide

```text
请为这个 notebook 生成一个适合 Audio Overview 的引导说明：

目标听众：我自己
希望风格：密度高、少寒暄、重点讲判断和冲突
重点问题：
1. {问题一}
2. {问题二}
3. {问题三}

请先列出建议强调的 5 个重点，再生成一段可作为 Audio Overview 指令的说明。
```

### 6. Write Back to Notion

```text
请把本次研究整理成适合粘贴回 Notion Research Pack 的格式：

## Key Takeaways
1.
2.
3.

## Evidence Table
| Claim | Evidence | Source | Confidence |

## Decision

## Follow-up Actions

## Evergreen Notes to Create
```

## 什么时候结束一个 NotebookLM 研究

满足以下任意 3 条，就可以结束：

- 已经回答 Research Question。
- 已经形成 Decision。
- 已经提炼出 Evergreen Notes。
- 没有新的高质量来源需要加入。
- 下一步已经变成行动，而不是继续研究。

结束后，把 NotebookLM URL 放回 Notion Research Pack，并把 Status 改成 `Done`。
