# Notion + Onyx Cloud + NotebookLM Knowledge Stack

这套方案的目标是把你的个人知识库分成三层：

- Notion: 长期知识沉淀、结构化管理、项目和研究记录。
- Onyx Cloud: 全库检索、带引用问答、多源知识 Agent。
- NotebookLM: 专题研究工作台，用来消化 Source Pack、问答和生成研究结论。

推荐主链路：

```text
日常记录 -> Notion
全库检索 -> Onyx Cloud
专题研究 -> NotebookLM Source Pack / local Mac upload
结论沉淀 -> Notion
再次索引 -> Onyx Cloud
```

## 文件说明

- `notion-blueprint.md`: Notion 顶层页面、数据库字段、视图和模板。
- `user-manual.md`: 日常使用手册，从 Notion、Onyx、NotebookLM 怎么开始。
- `onyx-cloud-setup.md`: Onyx Cloud 连接 Notion、权限范围、Agent 配置和验收问题。
- `notebooklm-local-sync.md`: 云端缓存 Source Pack，本机 Mac 下载并上传 NotebookLM 的方案。
- `ima-workflow.md`: ima 方案实测记录，当前不作为主链路。
- `notebooklm-workflow.md`: NotebookLM 如何与 Notion/Onyx 配合，不做脆弱的强同步。
- `notebooklm-sync-automation.md`: Notion Research Pack 导出为 NotebookLM Source Pack 的自动化流程。
- `hermes-feishu-chain.md`: 飞书 Agent -> Hermes -> Notion/Onyx/NotebookLM 缓存的云端自动化链路。
- `github-archive.md`: Notion 知识库单向镜像到私有 GitHub repo 的标准版归档方案。
- `weekly-operating-system.md`: 日常、每周、每月维护 SOP。
- `templates/*.csv`: 可导入 Notion 的数据库起始模板。

## 推荐落地顺序

如果你只是想开始使用，先读 `user-manual.md`。

1. 在 Notion 新建顶层页面 `AI Knowledge Hub`。
2. 导入 `templates/research-packs.csv`、`templates/source-library.csv`、`templates/evergreen-notes.csv`。
3. 按 `notion-blueprint.md` 调整字段类型和视图。
4. 在 Notion 创建一个只读 integration，授权给 `AI Knowledge Hub`。
5. 在 Onyx Cloud 配置 Notion connector。
6. 按 `onyx-cloud-setup.md` 创建 3-4 个 Agent。
7. 用 `onyx-cloud-setup.md` 的验收问题测试检索质量。
8. 遇到需要深度消化的主题，用飞书发 `kb research <主题>`，Hermes 会创建 Research Pack。
9. 需要把 Research Pack 扔给 NotebookLM 时，使用 `kb sync <Research Pack 标题>` 重新生成 Source Pack；你的 Mac 会在在线时下载并尝试通过本机 Chrome 上传。

## 第一阶段成功标准

7 天内只追求这几个效果：

- 你能在 Onyx 里问到 Notion 里的高价值笔记。
- 回答能给出可追溯来源。
- NotebookLM 只用于专题研究，不扰乱主知识库。
- 每次研究完成后，能产出一条 `Research Pack` 和若干条 `Evergreen Note`。

先不要把 NotebookLM 当成长期知识库。你的系统应该先稳定、可控、低维护。

## NotebookLM Source Pack 自动化

当前已支持把 Notion Research Pack 导出成 NotebookLM 可用的 Source Pack：

- 飞书命令：`kb research <主题>`
- 追加内容：`kb add <Research Pack 标题>` 换行输入新内容
- 重新导出：`kb sync <Research Pack 标题>`
- 云端输出：Notion Research Pack、Onyx 可索引内容、NotebookLM Source Pack、签名下载链接
- 本机输出：Mac 本地缓存目录和 `upload_queue.json`
- GitHub 输出：私有 repo 中的版本化 Markdown 归档和脱敏 manifest

下一次导出可以直接运行：

```bash
python3 knowledge-base-stack/scripts/export_notion_to_notebooklm_pack.py \
  --token-file /private/tmp/notion_api_key.txt \
  --target-label NotebookLM \
  --output-dir knowledge-base-stack/notebooklm-source-packs \
  --research-pack-title "2026-06 中学生英语提升与高考优胜策略" \
  --update-notion
```

生成文件位于：

```text
knowledge-base-stack/notebooklm-source-packs/
```

NotebookLM 上传不在腾讯云执行。云端只缓存 Source Pack；本机 Mac 通过 `scripts/notebooklm_local_sync.py` 下载，再由 `scripts/notebooklm_local_upload.js` 用本机 Chrome / Google 登录态上传。
