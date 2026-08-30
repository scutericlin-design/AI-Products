# Hermes Feishu Knowledge Chain

目标：把飞书 Agent、Hermes 云端、Notion、Onyx、LLM、NotebookLM Source Pack 缓存串成一个可 24 小时运行的知识库链路。

## Runtime Flow

```text
Feishu message
  -> POST /api/feishu/knowledge/events
  -> KnowledgeJob
  -> LLM Research Pack JSON
  -> Notion Research Pack
  -> NotebookLM Source Pack
  -> Cloud cache and signed download links
  -> GitHub private archive
  -> Onyx Notion connector / optional sync webhook
  -> Feishu completion message
  -> Mac local sync downloads Source Pack
  -> Mac local uploader imports Source Pack into NotebookLM
```

NotebookLM / Google 不在腾讯云上执行。稳定方案是“云端缓存 + 本机同步”：

1. Hermes 自动生成或更新 Notion Research Pack。
2. Hermes 自动导出 NotebookLM Source Pack。
3. Hermes 自动把 Markdown Source Pack 镜像到私有 GitHub repo。
4. Onyx 通过 Notion connector 索引长期沉淀内容。
5. 飞书回执返回 Markdown / Text 签名下载链接和 GitHub Archive commit。
6. 你的 Mac 开机且 VPN 可用时，本机同步器下载 Source Pack。
7. 本机上传器通过 Chrome / NotebookLM 登录态把 `.txt` 作为 Copied text 来源上传到 NotebookLM。

## New Endpoints

### Feishu event callback

```text
POST /api/feishu/knowledge/events
```

把飞书开放平台事件订阅的消息事件指向这个地址。支持 Feishu URL verification challenge。

如果没有配置 `FEISHU_VERIFICATION_TOKEN`，需要在回调 URL 上带共享密钥：

```text
POST /api/feishu/knowledge/events?secret=<KNOWLEDGE_COMMAND_SECRET>
```

### Internal command API

```text
POST /api/knowledge/commands
GET  /api/knowledge/jobs
GET  /api/knowledge/jobs/{job_key}
GET  /api/knowledge/jobs/{job_key}/source-pack/md
GET  /api/knowledge/jobs/{job_key}/source-pack/txt
GET  /api/knowledge/help
```

如果设置了 `KNOWLEDGE_COMMAND_SECRET`，请求需要带：

```text
X-Knowledge-Secret: <secret>
```

示例：

```bash
curl -X POST https://YOUR_DOMAIN/api/knowledge/commands \
  -H 'Content-Type: application/json' \
  -H 'X-Knowledge-Secret: YOUR_SECRET' \
  -d '{"text":"kb research 如何提高中学生英语水平，并在高考中取得优异成绩","requester":"eric","source":"manual"}'
```

## Feishu Commands

```text
kb research <主题>
kb add <Notion Research Pack 标题>
<新内容>
kb sync <Notion Research Pack 标题>
kb status <任务号>
kb help
```

推荐在飞书里使用：

```text
kb research 如何提高中学生英语水平，并在高考中取得优异成绩
```

给已有专题追加内容并重新导出 Source Pack：

```text
kb add 2026-06 中学生英语提升与高考优胜策略
新资料：
- 今天补充了一个课堂观察：学生阅读失分主要集中在长难句断句和证据定位。
- 后续训练应增加每篇阅读后的 evidence sentence 标注。
```

完成后飞书会返回：

- KnowledgeJob 任务号
- Notion Research Pack 链接
- Source Pack 路径
- Markdown / Text 下载链接
- NotebookLM 入口
- 下一步研究提示

## Required Environment

基础：

```env
KNOWLEDGE_ENABLED=true
KNOWLEDGE_COMMAND_SECRET=change-me
KNOWLEDGE_PUBLIC_BASE_URL=http://182.254.227.131
KNOWLEDGE_DOWNLOAD_TOKEN_TTL_SECONDS=604800
KNOWLEDGE_STUDY_WORKSPACE=notebooklm
KNOWLEDGE_STUDY_WORKSPACE_LABEL=NotebookLM
KNOWLEDGE_STACK_DIR=/app/knowledge-base-stack
KNOWLEDGE_SOURCE_PACKS_DIR=/app/knowledge-base-stack/notebooklm-source-packs
KNOWLEDGE_GITHUB_ARCHIVE_ENABLED=true
KNOWLEDGE_GITHUB_ARCHIVE_REPO_URL=git@github.com:scutericlin-design/personal-knowledge-archive.git
KNOWLEDGE_GITHUB_ARCHIVE_DIR=/app/data/knowledge-github-archive
KNOWLEDGE_GITHUB_ARCHIVE_SSH_KEY_FILE=/app/data/knowledge-github-archive-deploy-key
NOTION_API_KEY=...
NOTION_SETUP_JSON=/app/knowledge-base-stack/notion-setup-result.json
```

LLM。SiliconFlow 示例：

```env
LLM_PROVIDER=SiliconFlow
SILICONFLOW_API_KEY=...
SILICONFLOW_MODEL=Qwen/Qwen2.5-7B-Instruct
```

飞书回执。二选一即可：

```env
FEISHU_VERIFICATION_TOKEN=...
FEISHU_BOT_APP_ID=...
FEISHU_BOT_APP_SECRET=...
```

或沿用群机器人 webhook：

```env
KNOWLEDGE_FEISHU_WEBHOOK_URL=...
```

Google Drive 同步。当前本机同步方案不需要：

```env
GOOGLE_DRIVE_ENABLED=false
GOOGLE_DRIVE_FOLDER_ID=...
GOOGLE_SERVICE_ACCOUNT_FILE=/app/data/google-service-account.json
GOOGLE_DRIVE_SHARE_WITH_EMAIL=your-google-account@example.com
```

也可以用单行 JSON：

```env
GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
```

Onyx：

```env
ONYX_WORKSPACE_URL=https://cloud.onyx.app/app
ONYX_SYNC_WEBHOOK_URL=
ONYX_API_KEY=
```

默认只依赖 Onyx 的 Notion connector 自动索引；如果你后续有 Onyx 可用的刷新 webhook，再填 `ONYX_SYNC_WEBHOOK_URL`。

NotebookLM：

```env
NOTEBOOKLM_WORKSPACE_URL=https://notebooklm.google.com/
NOTEBOOKLM_DEFAULT_NOTEBOOK_URL=
```

## Google Drive Setup

仅在改回 Google Drive 云桥路径时需要。当前方案不需要。

1. 在 Google Cloud 创建 Service Account。
2. 启用 Google Drive API 和 Google Docs API。
3. 下载 service account JSON，放到服务器 `/app/data/google-service-account.json`。
4. 在 Google Drive 建一个文件夹，例如 `Hermes NotebookLM Source Packs`。
5. 把这个文件夹共享给 service account 的 email，并给编辑权限。
6. 把文件夹 ID 填到 `GOOGLE_DRIVE_FOLDER_ID`。
7. 把你的 Google 账号填到 `GOOGLE_DRIVE_SHARE_WITH_EMAIL`，Hermes 创建的文档会共享给你。

## Deployment Notes

`docker-compose.yml` 和 `docker-compose.prod.yml` 已挂载：

```text
./knowledge-base-stack:/app/knowledge-base-stack
```

这样 Research Pack 输入文件和 NotebookLM Source Pack 会保存在服务器项目目录里。

重启：

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

检查：

```bash
curl https://YOUR_DOMAIN/health
curl -H 'X-Knowledge-Secret: YOUR_SECRET' https://YOUR_DOMAIN/api/knowledge/help
```

## Reliability Boundary

稳定自动化：

- 飞书命令
- Hermes 任务记录
- LLM 生成研究包
- Notion 创建/更新专题
- NotebookLM Source Pack 导出
- Source Pack 云端缓存
- Markdown / Text 签名下载链接
- Onyx Notion connector 自动索引
- 飞书状态回执
- 本机 Source Pack 下载队列

本机自动 / 半自动：

- NotebookLM Copied text 上传，需要本机 VPN 和 Google 登录态
- NotebookLM Studio 输出生成

原因：NotebookLM 当前没有稳定公开的 source 创建 API；腾讯云访问 Google 也不稳定。自动化核心仍然是 Hermes + Notion + Onyx + LLM，Google/NotebookLM 侧放到你的 Mac 本机处理。
