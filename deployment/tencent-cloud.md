# 腾讯云轻量应用服务器部署手册

目标：把赤霄 Alpha / Hermes Agent 以 Docker + Caddy 的方式部署到腾讯云轻量应用服务器。单机版默认继续使用 SQLite，数据保存在服务器项目目录的 `data/app.db`，以后迁移到 PostgreSQL 只需要改 `DATABASE_URL`。

## 1. 腾讯云控制台

1. 服务器系统建议选择 Ubuntu 22.04 LTS、Ubuntu 24.04 LTS，或腾讯云默认的 OpenCloudOS 9。部署脚本会自动识别 `apt-get` / `dnf` / `yum`。
2. 防火墙/安全组放行：
   - `22`：SSH 登录。
   - `80`：网页访问。
   - `443`：以后配置 HTTPS 时使用。
3. 记录服务器公网 IP。

## 2. 本地打包

在项目根目录运行：

```bash
bash deployment/pack_tencent.sh
```

脚本会生成：

```text
deployment/dist/chixiao-alpha-tencent-YYYYMMDD_HHMMSS.tar.gz
```

默认包只包含代码和部署配置，不包含本地 `data/`。这适合日常升级，避免覆盖云端用户、持仓、观察池和 TuShare token。

如果这是第一次上云，并且你想把本机当前状态一起迁移到腾讯云，使用：

```bash
bash deployment/pack_tencent.sh --include-data
```

这个迁移包会包含 `data/app.db`、已生成的股票池结果和 TuShare 缓存。只建议首次部署使用，后续升级不要带 `--include-data`。

## 3. 上传到服务器

推荐直接使用自动部署脚本，把下面的 `SERVER_IP` 替换成你的服务器公网 IP：

```bash
SSH_KEY=~/.ssh/chixiao_alpha_tencent bash deployment/deploy_to_tencent.sh SERVER_IP root /opt/chixiao-alpha
```

首次迁移本地数据时：

```bash
SSH_KEY=~/.ssh/chixiao_alpha_tencent bash deployment/deploy_to_tencent.sh --include-data SERVER_IP root /opt/chixiao-alpha
```

如果你不是 root 用户，把 `root` 换成实际登录用户名。如果不使用专用 SSH key，去掉前面的 `SSH_KEY=...`。

如果想手动上传，只传刚生成的那个包：

```bash
ARCHIVE="$(bash deployment/pack_tencent.sh)"
scp "$ARCHIVE" root@SERVER_IP:/opt/
```

手动首次迁移本地数据：

```bash
ARCHIVE="$(bash deployment/pack_tencent.sh --include-data)"
scp "$ARCHIVE" root@SERVER_IP:/opt/
```

## 4. 服务器初始化

登录服务器：

```bash
ssh root@SERVER_IP
```

解压并安装 Docker：

```bash
cd /opt
mkdir -p chixiao-alpha
tar -xzf chixiao-alpha-tencent-*.tar.gz -C chixiao-alpha
cd chixiao-alpha
bash deployment/server_setup_ubuntu.sh
```

如果当前不是 root 用户，使用：

```bash
sudo bash deployment/server_setup_ubuntu.sh
```

## 5. 配置环境变量

复制配置文件：

```bash
cp .env.example .env
```

编辑 `.env`：

```bash
nano .env
```

关键配置：

```text
APP_NAME=赤霄 Alpha
DATABASE_URL=sqlite:////app/data/app.db
APP_SECRET=请替换为迁移前本地使用的同一个 APP_SECRET
MARKET_DATA_CACHE_BACKEND=database
APP_IP_ADDRESS=182.254.227.131
APP_SITE_ADDRESS=app.chixiaoalpha.cn
TLS_ADMIN_EMAIL=admin@chixiaoalpha.cn
```

非常重要：如果本地数据库里已经保存了 TuShare token，云端必须使用同一个 `APP_SECRET`，否则云端无法解密原 token。当前本地默认值是 `local-dev-change-me-before-cloud-deploy`；如果之前没有改过，云端也先保持一致，等重新配置 TuShare token 后再更换。

如果暂时没有域名，保持 `APP_SITE_ADDRESS=:80`，浏览器访问 `http://SERVER_IP/`。如果已经把域名解析到服务器，例如 `app.example.com`，改成：

```text
APP_SITE_ADDRESS=app.example.com
TLS_ADMIN_EMAIL=you@example.com
```

Caddy 会自动申请和续期 HTTPS 证书。

## 6. 启动服务

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

查看状态：

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f --tail=100
```

浏览器打开：

```text
http://SERVER_IP/
```

如果你迁移了本地 `data/app.db`，本地已有用户和 admin 权限会一起迁移。

如果这是空库首次部署，先打开网页注册你的账号，然后在服务器项目目录运行：

```bash
docker compose -f docker-compose.prod.yml exec web python scripts/promote_admin.py your@email.com
```

把 `your@email.com` 换成刚注册的邮箱。之后刷新网页重新登录即可进入 Admin 维护端。

## 7. 后续升级代码

以后本地改完代码后：

```bash
SSH_KEY=~/.ssh/chixiao_alpha_tencent bash deployment/deploy_to_tencent.sh SERVER_IP root /opt/chixiao-alpha
```

如果手动升级，传刚生成的包：

```bash
ARCHIVE="$(bash deployment/pack_tencent.sh)"
scp "$ARCHIVE" root@SERVER_IP:/opt/
```

然后服务器上执行：

```bash
cd /opt/chixiao-alpha
LATEST_ARCHIVE="$(ls -t /opt/chixiao-alpha-tencent-*.tar.gz | head -n 1)"
tar -xzf "$LATEST_ARCHIVE" -C /opt/chixiao-alpha
docker compose -f docker-compose.prod.yml up -d --build
```

如果使用 `deployment/deploy_to_tencent.sh` 自动部署，脚本会在发现服务器已有 `data/app.db` 时先备份服务器 `data/`，解压代码后再恢复服务器数据，避免升级代码时覆盖云端用户、持股、观察池和 TuShare token。

日常升级不要带 `--include-data`。即使误带了，自动部署脚本在发现服务器已有数据库时也会优先恢复服务器数据。

如果你手动解压升级，建议先备份服务器数据：

```bash
cd /opt/chixiao-alpha
cp -a data /tmp/chixiao-alpha-data-$(date +%Y%m%d_%H%M%S)
tar -xzf /opt/chixiao-alpha-tencent-*.tar.gz -C /opt/chixiao-alpha
docker compose -f docker-compose.prod.yml up -d --build
```

`data/` 是持久化目录，不会因为重建 Docker 镜像丢失，但手动解压包含 `data/` 的部署包时仍需要先备份。

## 8. 备份

最重要的是备份数据库：

```bash
cd /opt/chixiao-alpha
mkdir -p backups
cp data/app.db backups/app-$(date +%Y%m%d_%H%M%S).db
```

下载备份到本地：

```bash
scp root@SERVER_IP:/opt/chixiao-alpha/backups/app-*.db ./backups/
```

## 9. 常用运维命令

```bash
docker compose -f docker-compose.prod.yml restart
docker compose -f docker-compose.prod.yml logs -f --tail=100
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d --build
```

## 10. HTTPS

没有域名时先用 `http://SERVER_IP/` 访问即可。有域名后，把域名解析到服务器公网 IP，把 `.env` 里的 `APP_SITE_ADDRESS` 改为域名，然后重启：

```bash
docker compose -f docker-compose.prod.yml up -d
```

Caddy 会自动启用 HTTPS。
