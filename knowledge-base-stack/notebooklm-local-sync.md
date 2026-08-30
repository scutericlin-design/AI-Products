# NotebookLM Local Sync

目标：NotebookLM 继续作为专题研究室，但 Google/NotebookLM 动作不在腾讯云上执行。优先用 Google Drive Doc 作为 NotebookLM 稳定来源；Copied text 上传只作为兜底。

## 架构

```text
飞书命令
  -> Hermes 云端
  -> Notion Research Pack
  -> NotebookLM Source Pack
  -> 云端缓存和签名下载链接
  -> 你的 Mac 开机后本机同步器下载
  -> 本机 OAuth 更新 Google Drive Doc
  -> Google Drive Doc 自动创建/更新
  -> 如需 NotebookLM，再手动把 Drive Doc 加入 NotebookLM
```

这样做的原因：

- 腾讯云访问 Google 不稳定。
- 你的 Mac 有 VPN，可以稳定访问 Google 和 NotebookLM。
- NotebookLM 没有稳定公开的服务端 source 创建 API。
- Source Pack 生成、缓存、索引和飞书通知仍然可以 24 小时云端运行。
- 腾讯云直连 Google API 会超时，所以 Drive 写入放在本机或后续 Google 侧 runner。

## 云端配置

```env
KNOWLEDGE_STUDY_WORKSPACE=notebooklm
KNOWLEDGE_STUDY_WORKSPACE_LABEL=NotebookLM
KNOWLEDGE_SOURCE_PACKS_DIR=/app/knowledge-base-stack/notebooklm-source-packs
GOOGLE_DRIVE_ENABLED=false
KNOWLEDGE_PUBLIC_BASE_URL=http://182.254.227.131
```

飞书任务完成后会返回：

- Notion Research Pack 链接
- 云端 Source Pack 路径
- Markdown 下载链接
- Text 下载链接
- NotebookLM 入口

## 本机同步器

手动跑一次：

```bash
HERMES_KNOWLEDGE_SECRET='你的知识库密钥' \
python3 knowledge-base-stack/scripts/notebooklm_local_sync.py \
  --cloud-base-url http://182.254.227.131 \
  --open-notebooklm
```

默认下载到：

```text
~/Documents/Hermes NotebookLM Source Packs/
```

目录里会生成：

- 每个任务一个子目录
- `.md` Source Pack
- `.txt` Source Pack
- `manifest.json`
- `upload_queue.json`

## 开机自动同步

安装 LaunchAgent：

```bash
export HERMES_KNOWLEDGE_SECRET='你的知识库密钥'
bash knowledge-base-stack/scripts/install_notebooklm_local_sync_launch_agent.sh
```

它会在 Mac 登录时启动，并每 5 分钟检查一次云端新任务。

当前安装时已开启本机 Drive 同步：

```env
NOTEBOOKLM_SYNC_DRIVE=true
GOOGLE_DRIVE_FOLDER_ID=1pZQblM3TRdVZdPQzobKfvBlnnD7u6c93
GOOGLE_OAUTH_TOKEN_FILE=~/Library/Application Support/Hermes/google-oauth-token.json
```

日志：

```text
~/Library/Logs/Hermes/notebooklm-local-sync.out.log
~/Library/Logs/Hermes/notebooklm-local-sync.err.log
```

## 上传到 NotebookLM

当前稳定自动化做到：

1. 云端生成 NotebookLM Source Pack。
2. 飞书返回下载链接。
3. 本机同步器下载到 Mac。
4. 本机生成上传队列。
5. 本机同步器用 OAuth token 创建或更新 Google Drive Doc。
6. 成功后把 `upload_queue.json` 中的任务标记为 `drive_synced`，不再自动上传到 NotebookLM。

当前“2026-06 中学生英语提升与高考优胜策略”专题已经把 Drive Doc 添加到 NotebookLM，专题页显示 `3 个来源`。后续同一个 Drive Doc 更新后，NotebookLM 可以继续以该 Drive Doc 作为稳定来源。

新专题的默认流程是：

```text
Research Pack 主题
  -> Google Drive Doc: NotebookLM Source Pack - <主题>
  -> upload_queue.json 标记为 drive_synced
  -> 后续同主题研究继续更新同一个 Google Drive Doc
```

Copied text 上传器仍保留为兜底。第一次使用上传器前，先打开一个专用 Chrome Profile 并完成 Google 登录：

```bash
node knowledge-base-stack/scripts/notebooklm_local_upload.js --setup-login
```

登录完成后，手动跑一次上传：

```bash
node knowledge-base-stack/scripts/notebooklm_local_upload.js
```

安装后台自动上传：

```bash
bash knowledge-base-stack/scripts/install_notebooklm_local_upload_launch_agent.sh
```

它会每 5 分钟检查一次 `upload_queue.json`，有新任务就上传到 NotebookLM。

日志：

```text
~/Library/Logs/Hermes/notebooklm-local-upload.out.log
~/Library/Logs/Hermes/notebooklm-local-upload.err.log
```

NotebookLM 上传本身依赖本机浏览器登录态。首次验证时需要你配合：

- 确保 VPN 开启。
- 确保专用 Chrome Profile 已登录 Google。
- 确认 NotebookLM 可以正常打开。

NotebookLM 上传器只作为手动兜底工具保留，不随 Drive 同步自动执行。NotebookLM 没有稳定公开 API，因此如果以后需要把 Drive Doc 加入 NotebookLM，建议首次人工添加；Drive Doc 后续更新会继续作为稳定来源。
