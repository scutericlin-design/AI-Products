# 腾讯云轻量应用服务器部署手册

目标：把赤霄 Alpha 以 Docker + Nginx 的方式部署到腾讯云轻量应用服务器。单机版默认继续使用 SQLite，数据保存在服务器项目目录的 `data/app.db`，以后迁移到 PostgreSQL 只需要改 `DATABASE_URL`。

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

这个包包含代码、`data/app.db`、已生成的股票池结果和 TuShare 数据库缓存。

## 3. 上传到服务器

把下面的 `SERVER_IP` 替换成你的服务器公网 IP：

```bash
scp deployment/dist/chixiao-alpha-tencent-*.tar.gz root@SERVER_IP:/opt/
```

如果你不是 root 用户，把 `root` 换成实际登录用户名。

如果使用专用 SSH key：

```bash
SSH_KEY=~/.ssh/chixiao_alpha_tencent bash deployment/deploy_to_tencent.sh SERVER_IP root /opt/chixiao-alpha
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
```

非常重要：如果本地数据库里已经保存了 TuShare token，云端必须使用同一个 `APP_SECRET`，否则云端无法解密原 token。当前本地默认值是 `local-dev-change-me-before-cloud-deploy`；如果之前没有改过，云端也先保持一致，等重新配置 TuShare token 后再更换。

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

Admin 初始账号：

```text
admin@chixiaoalpha.com
Admin@2026!
```

登录后建议马上在 Admin 维护端禁用不需要的测试客户，并修改/重建正式 admin 账号。

## 7. 后续升级代码

以后本地改完代码后：

```bash
bash deployment/pack_tencent.sh
scp deployment/dist/chixiao-alpha-tencent-*.tar.gz root@SERVER_IP:/opt/
```

服务器上执行：

```bash
cd /opt/chixiao-alpha
tar -xzf /opt/chixiao-alpha-tencent-*.tar.gz -C /opt/chixiao-alpha
docker compose -f docker-compose.prod.yml up -d --build
```

如果使用 `deployment/deploy_to_tencent.sh` 自动部署，脚本会在发现服务器已有 `data/app.db` 时先备份服务器 `data/`，解压代码后再恢复服务器数据，避免升级代码时覆盖云端用户、持股、观察池和 TuShare token。

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

有域名后，把域名解析到服务器公网 IP，然后可以把 Nginx 换成 Caddy 或用 Certbot 配 HTTPS。没有域名时先用 `http://SERVER_IP/` 访问即可。
