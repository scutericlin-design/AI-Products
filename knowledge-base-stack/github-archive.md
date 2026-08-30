# GitHub Knowledge Archive

目标：把 Notion 知识库的可归档成果单向镜像到一个私有 GitHub repo。Notion 仍然是唯一事实源；GitHub 只做版本化归档、备份、回滚和 Agent 可读镜像。

## 当前实现

Hermes 在每次知识库任务完成时自动尝试归档：

- `kb research <主题>`
- `kb sync <Research Pack 标题>`
- `kb add <Research Pack 标题>` 换行 `<新内容>`

归档步骤在 Notion 导出、Drive 同步和研究工作台结果生成之后执行。GitHub 归档失败不会阻断主链路，失败状态会写入任务结果和飞书回执。

## Repo 结构

```text
README.md
index.json
research-packs/
  <topic-slug>.md
source-packs/
  notebooklm/
    <topic-slug>/
      <job-key>.md
      latest.md
manifests/
  notebooklm/
    <topic-slug>/
      <job-key>.json
```

说明：

- `research-packs/<topic>.md` 是该专题最新版本，方便人和 Agent 快速读取。
- `source-packs/<workspace>/<topic>/<job-key>.md` 是每次导出的版本快照，可追溯。
- `source-packs/<workspace>/<topic>/latest.md` 指向最新内容。
- `manifests/` 只保存脱敏元数据，不保存 API key、OAuth token 或本机绝对密钥路径。
- 默认不归档 `.txt`，避免和 Markdown 重复；如需要可打开 `KNOWLEDGE_GITHUB_ARCHIVE_INCLUDE_TEXT=true`。

## 云端配置

```env
KNOWLEDGE_GITHUB_ARCHIVE_ENABLED=true
KNOWLEDGE_GITHUB_ARCHIVE_REPO_URL=git@github.com:scutericlin-design/personal-knowledge-archive.git
KNOWLEDGE_GITHUB_ARCHIVE_DIR=/app/data/knowledge-github-archive
KNOWLEDGE_GITHUB_ARCHIVE_BRANCH=main
KNOWLEDGE_GITHUB_ARCHIVE_PATH_PREFIX=
KNOWLEDGE_GITHUB_ARCHIVE_AUTHOR_NAME=Hermes Knowledge Bot
KNOWLEDGE_GITHUB_ARCHIVE_AUTHOR_EMAIL=hermes-knowledge-bot@users.noreply.github.com
KNOWLEDGE_GITHUB_ARCHIVE_SSH_KEY_FILE=/app/data/knowledge-github-archive-deploy-key
KNOWLEDGE_GITHUB_ARCHIVE_INCLUDE_TEXT=false
KNOWLEDGE_GITHUB_ARCHIVE_PUSH=true
```

## GitHub 准备

建议创建一个独立私有 repo：

```text
scutericlin-design/personal-knowledge-archive
```

不要把这个归档混进当前代码仓库。代码仓库负责运行系统，知识归档仓库负责保存知识成果。

## Deploy Key

在腾讯云宿主机生成专用 deploy key：

```bash
ssh-keygen -t ed25519 -C "hermes-knowledge-archive" -f /opt/chixiao-alpha/data/knowledge-github-archive-deploy-key -N ""
cat /opt/chixiao-alpha/data/knowledge-github-archive-deploy-key.pub
```

把输出的 public key 添加到 GitHub repo：

```text
Repo -> Settings -> Deploy keys -> Add deploy key
Title: Hermes Knowledge Archive
Allow write access: checked
```

私钥只保存在腾讯云 `/opt/chixiao-alpha/data/`，容器内路径是 `/app/data/knowledge-github-archive-deploy-key`。

## 验证

配置完成并部署后，在飞书发送：

```text
kb sync 2026-06 中学生英语提升与高考优胜策略
```

完成后飞书回执应包含：

```text
GitHub Archive：<commit-sha>
```

也可以在云端检查：

```bash
cd /opt/chixiao-alpha/data/knowledge-github-archive
git log --oneline -5
git status --short
```

## 边界

- GitHub 是只读镜像，不作为日常编辑入口。
- 不归档 Inbox 原始碎片，除非它们已经进入 Research Pack 并被导出。
- 不归档密钥、OAuth token、服务账号 JSON 或数据库。
- 归档失败不影响 Notion、Drive、NotebookLM 主流程。
