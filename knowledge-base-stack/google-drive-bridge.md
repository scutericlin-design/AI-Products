# Google Drive Bridge

目标：用 Google Drive Doc 作为 NotebookLM 的稳定来源层。NotebookLM 只需要首次添加这个 Drive Doc，后续 Hermes 更新同一个 Doc 即可。

## 当前落地状态

- Google OAuth app 已发布到正式版。
- 已启用 Google Drive API 和 Google Docs API。
- 已生成 `scutericlin@gmail.com` 的 Drive / Docs OAuth refresh token。
- 已验证可以创建/更新 Drive Doc：

```text
https://docs.google.com/document/d/108Tfg2YwqJgNMOfR1wnSA_LcFuR6ThTjBAlyA23XAE0/edit?usp=drivesdk
```
- 已把这个 Drive Doc 添加到 NotebookLM 专题：

```text
https://notebooklm.google.com/notebook/f2448fb3-58ce-4feb-9e63-da2eb2fb57a0
```

当前 NotebookLM 专题显示为 `3 个来源`，其中包含：

```text
NotebookLM Source Pack - 2026-06 中学生英语提升与高考优胜策略
```

## 为什么不是腾讯云直接写 Drive

腾讯云服务器到 Google API 访问超时：

- `oauth2.googleapis.com`
- `www.googleapis.com`
- `docs.googleapis.com`

所以不要让腾讯云直接调用 Google Drive API。否则每次知识库任务都会在 Drive 步骤等待超时。

## 当前主链路

```text
飞书命令
  -> Hermes 腾讯云生成 Notion Research Pack 和 Source Pack
  -> 云端缓存 Markdown/Text Source Pack
  -> Mac 本机同步器下载 Source Pack
  -> Mac 本机 OAuth token 更新 Google Drive Doc
  -> Google Drive Doc 自动创建/更新
  -> 如需 NotebookLM，手动把 Drive Doc 作为来源加入专题
```

这个方案的特点：

- 不需要每次登录 Google。
- 不需要每次用浏览器上传文件。
- 电脑在线且 VPN 可用时自动更新 Drive Doc。
- NotebookLM 首次添加 Drive Doc 后，后续以 Drive Doc 为稳定来源。

## 本机后台任务

本机 LaunchAgent：

```text
~/Library/LaunchAgents/com.hermes.notebooklm-local-sync.plist
```

默认每 5 分钟：

1. 拉取 Hermes 云端已完成任务。
2. 下载 Source Pack 到本机。
3. 用 OAuth token 更新 Google Drive Doc。
4. 把队列项标记为 `drive_synced`。

缓存目录：

```text
~/Documents/Hermes NotebookLM Source Packs/
```

OAuth token：

```text
~/Library/Application Support/Hermes/google-oauth-token.json
```

## 未来新专题仍需一次性完成

当前“2026-06 中学生英语提升与高考优胜策略”专题已经完成首次 Drive Doc 添加。

每个新主题都会自动创建或更新对应的 Google Drive Doc。是否把这个 Drive Doc 加到 NotebookLM 专题里，改为人工决定；NotebookLM 上传器不再随后台任务自动执行。

如果要做到真正 24 小时且完全不依赖 Mac，需要下一步把 Drive Bridge 搬到 Google 侧 runner，例如 Google Cloud Run 或 Apps Script 定时器，让 Google 侧主动拉 Hermes 云端 Source Pack 并更新 Drive Doc。
