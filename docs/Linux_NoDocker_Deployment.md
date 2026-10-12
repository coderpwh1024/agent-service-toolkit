# CentOS 非 Docker 部署

本部署包使用 Python 虚拟环境运行 FastAPI 和 Streamlit，支持 systemd 自动启动和异常重启。以下命令以 **CentOS Stream 9 / 10**、安装目录 `/opt/agent-service-toolkit` 为例，使用 DNF、原生 PostgreSQL 和 Nginx。服务器需要联网下载 Python 和依赖，并能访问模型服务；此包是源码部署包，不含 Python、第三方依赖或数据库数据。

## 0. 确认 CentOS 版本和兼容性

先在目标服务器执行：

```sh
cat /etc/centos-release
uname -m
getconf GNU_LIBC_VERSION
```

当前部署包针对 `x86_64` / `aarch64`、glibc **2.28 或更高版本**的环境。仓库 `uv.lock` 中的 PyArrow 23.0.1 Linux wheel 使用 `manylinux_2_28`；NumPy、ONNX Runtime 等依赖也不能直接使用 CentOS 7 的默认 glibc 环境。能安装 Python 并不代表全部依赖可运行。

| 系统 | 本方案的处理 |
| --- | --- |
| CentOS Stream 9 / 10 | 使用下文的 DNF 和 systemd 步骤 |
| CentOS Linux 8 / Stream 8 | 先确认 glibc 至少为 2.28；已停止维护，软件源和数据库版本需单独处理，下文命令不直接适用 |
| CentOS Linux 7 | 默认 glibc 2.17，不支持当前依赖包；应先迁移至兼容系统 |

安装脚本会在下载 Python、安装依赖或创建配置文件之前检查 glibc；版本不满足时明确退出。pip 安装路径同样受此限制。不要通过替换系统 glibc 或系统 Python 来套用此方案。CentOS 7 与 Stream 8 的维护状态见 [CentOS 官方说明](https://blog.centos.org/2023/04/end-dates-are-coming-for-centos-stream-8-and-centos-linux-7/)。

## 1. 在本地重新打包

已生成的 `output/agent-service-toolkit-*-linux-*.tar.gz` 可直接上传。以后修改代码后，可以使用 Python 3.12–3.14 和仓库固定的 uv 重新打包：

```sh
python3 scripts/package_linux.py
```

脚本收集当前工作区的源码（包括尚未提交的修改），并从 `uv.lock` 导出带哈希的生产依赖 `requirements.txt`。包中包含源码、SQL、部署脚本、配置模板和本说明；不包含 `.env`、私有凭据、知识库文件、Git 数据、Docker 配置、虚拟环境、缓存或运行日志。不复用 Mac 上的二进制依赖，Linux 服务器安装时根据自身架构选择依赖。

## 2. 上传、校验和解压

将 `.tar.gz` 和同名 `.sha256` 文件一起上传。在服务器的上传目录执行，下面的文件名需要替换为实际名称：

```sh
sha256sum -c agent-service-toolkit-VERSION-linux-TIMESTAMP.tar.gz.sha256

sudo useradd --system --user-group --create-home \
  --home-dir /var/lib/agent-service --shell /sbin/nologin agent-service
sudo mkdir -p /opt
sudo tar --no-same-owner -xzf agent-service-toolkit-VERSION-linux-TIMESTAMP.tar.gz -C /opt
sudo chown -R agent-service:agent-service /opt/agent-service-toolkit
cd /opt/agent-service-toolkit
```

已存在服务账号时跳过 `useradd`。升级时先停止应用、备份 PostgreSQL，并保留服务器的 `.env`；不要用新模板覆盖密钥。代码可以解压覆盖，但若新版删除了源文件，应使用新目录重新部署，避免旧文件残留。

## 3. 安装 Python 和依赖

安装基本工具及固定版本的 uv，然后以服务账号执行安装脚本：

```sh
sudo dnf install -y ca-certificates curl tar gzip libstdc++ policycoreutils
curl -LsSf https://astral.sh/uv/0.11.32/install.sh \
  | sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

sudo -u agent-service -H bash deploy/install.sh
sudo restorecon -RF /opt/agent-service-toolkit
```

脚本默认下载 Python **3.13.14**，安装至项目 `.python/`，将生产依赖安装至 `.venv/`，并在没有 `.env` 时创建权限为 `600` 的配置文件。已有 `.env` 会保留，不修改 CentOS 自带的 Python 或 `/usr/bin/python`。Python 和虚拟环境必须在最终安装目录创建；安装完成后不要移动目录。`restorecon` 恢复安装目录的 SELinux 文件上下文。uv 的版本安装方式和冻结依赖说明见 [uv 安装文档](https://docs.astral.sh/uv/getting-started/installation/)与 [uv 依赖同步文档](https://docs.astral.sh/uv/concepts/projects/sync/)。

如服务器已安装 Python 3.12–3.14，也可以使用包内的锁定依赖清单：

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.txt
cp .env.example .env
chmod 600 .env
```

pip 路径下应在服务账号或其拥有的安装目录操作；已有 `.env` 时跳过复制步骤，安装后也执行 `sudo restorecon -RF /opt/agent-service-toolkit`。部分依赖可能需要编译，届时按安装错误补充对应系统开发包。离线部署需要提前在相同 Linux 架构、glibc 和 Python 版本下下载依赖，此源码包本身不支持离线安装。

## 4. 准备数据库和运行配置

**PostgreSQL 是启动必需项**，可以连接现有 PostgreSQL 16，也可以原生安装。已有数据库时跳过安装和初始化，仅配置应用账号与连接信息。

CentOS Stream 9 使用 PostgreSQL 16 模块：

```sh
sudo dnf module list postgresql
sudo dnf module install -y postgresql:16/server
```

CentOS Stream 10 使用发行版的软件包，不执行 Stream 9 的模块命令：

```sh
sudo dnf install -y postgresql-server postgresql-contrib
```

然后初始化并启动数据库。`--initdb` 只在全新数据库上执行；已有数据目录时跳过，模块版本切换也必须先单独评估数据迁移：

```sh
sudo postgresql-setup --initdb
sudo systemctl enable --now postgresql.service
sudo -u postgres createuser --pwprompt agent_service
sudo -u postgres createdb --owner=agent_service agent_service
```

`createuser` 交互输入的密码需要与应用配置一致。PostgreSQL 16 默认使用 SCRAM 保存新密码。CentOS 的默认 TCP 认证规则可能使用 `ident`；在 `/var/lib/pgsql/data/pg_hba.conf` 中，**放在已有通用 localhost 规则之前**，增加仅针对本应用的规则：

```text
host    agent_service    agent_service    127.0.0.1/32    scram-sha-256
host    agent_service    agent_service    ::1/128         scram-sha-256
```

保留管理员的本地 socket 认证规则，然后重新加载并验证应用账号能够通过密码连接：

```sh
sudo systemctl reload postgresql.service
psql -h 127.0.0.1 -U agent_service -d agent_service -W -c 'SELECT 1;'
```

使用已有实例或 PGDG 软件包时，配置路径和服务名可能不同，应以实例实际配置为准。数据库账号必须有在该数据库内创建表和索引的权限；应用首次启动会初始化检查点、记忆与相关业务表，不会回退到 SQLite 或内存。已有用户和数据库时跳过创建。发行版数据库安装规则见 [PostgreSQL 官方说明](https://www.postgresql.org/download/linux/redhat/)，模块与认证配置见 [Red Hat PostgreSQL 文档](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/configuring_and_using_database_servers/using-postgresql_configuring-and-using-database-servers)。

编辑部署目录的 `.env`：

```sh
sudo -u agent-service vi .env
```

至少替换 `AUTH_SECRET`、`DASHSCOPE_API_KEY`、`POSTGRES_PASSWORD`；连接远程数据库时同时调整 `POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_DB` 和 `POSTGRES_USER`。`AUTH_SECRET` 使用足够长的随机值，网页界面和 API 会读取同一配置。

默认配置为 `MODE=prod`、关闭 Nacos、API 监听 `127.0.0.1:8000`、网页界面监听 `127.0.0.1:8501`。启动器会先把根 `.env` 装载至进程环境，再导入应用设置；显式进程环境变量仍优先。因此在源码仓库中使用本部署入口时，开发环境文件也不会覆盖 `.env` 中的部署值。配置变化后重启相应服务。

需要 Nacos 时，在 `.env` 中设置 `NACOS_ENABLED=true`、`NACOS_SERVER_ADDR=你的服务器:8848`、`NACOS_USERNAME`、`NACOS_PASSWORD` 和 `NACOS_CONFIG_DATA_ID=agent-service-toolkit.yaml`，并确保对应 gRPC 端口（默认 `9848`）可访问。使用部署自己的 Namespace、Group 和 PostgreSQL/Redis 配置；启用服务注册时再设置 `NACOS_REGISTER_SERVICE=true`、可达的 `NACOS_SERVICE_IP` 和 `NACOS_SERVICE_PORT=8000`。Nacos 中的认证密钥必须与网页界面读取的密钥保持一致。

默认关闭邮箱验证码和实时语音。启用邮箱认证时需要另行安装或连接 Redis，并配置 `EMAIL_AUTH_ENABLED=true`、`REDIS_URL`、SMTP 和 `APP_TOKEN_SECRET`。CentOS Stream 9 的本机 Redis 可按需安装：

```sh
sudo dnf install -y redis
sudo systemctl enable --now redis.service
```

CentOS Stream 10 已将 Redis 替换为 Valkey，不能直接照用上述 Redis 包名和服务名；需要保持原有 Redis 实现时，可连接已有 Redis 服务。若选择发行版的 Valkey，应单独验证邮箱验证码和限流流程后使用，本文未将其作为已验证的替代。包变化见 [CentOS Stream 10 发布说明](https://www.centos.org/centos10/)。实时语音及其他模型 Provider 按已有应用配置启用。

知识库文档和数据库数据需要独立迁移；代码包不会自动导入旧机器的会话记录或 RAG 数据。

## 5. 启动和探活

可以先在两个终端中前台启动，便于检查连接配置：

```sh
sudo -u agent-service -H .venv/bin/python deploy/run.py service
```

```sh
sudo -u agent-service -H .venv/bin/python deploy/run.py app
```

正式运行前结束前台进程，再安装随包的 systemd 配置：

```sh
sudo install -m 644 deploy/systemd/agent-service.service /etc/systemd/system/
sudo install -m 644 deploy/systemd/agent-streamlit.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-service agent-streamlit

curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8501/_stcore/health
sudo systemctl status agent-service agent-streamlit
```

只部署 API 时，仅安装和启用 `agent-service`。若使用其他账号或安装目录，先修改两个 `.service` 文件中的 `User`、`Group`、`WorkingDirectory` 和 `ExecStart`。

查看日志和重启：

```sh
sudo journalctl -u agent-service -u agent-streamlit -n 100 --no-pager
sudo systemctl restart agent-service agent-streamlit
```

探活通过表示进程和启动依赖可用，不能证明模型密钥可正常调用；真实对话需配置有效模型凭据，并可能产生模型费用。

## 6. 访问与 HTTPS

首次检查可从本机通过 SSH 转发访问，无需修改服务器监听配置：

```sh
ssh -L 8501:127.0.0.1:8501 -L 8000:127.0.0.1:8000 USER@SERVER
```

浏览器打开 `http://127.0.0.1:8501`，API 文档为 `http://127.0.0.1:8000/redoc`。

公网部署可使用随包的 Nginx 配置，将两个域名代理至本机端口：

```sh
sudo dnf install -y nginx
sudo install -m 644 deploy/nginx/agent-service-toolkit.conf /etc/nginx/conf.d/
sudo vi /etc/nginx/conf.d/agent-service-toolkit.conf
```

将 `app.example.com`、`api.example.com` 替换为已解析到服务器的域名。配置已传递 WebSocket 头，API 已关闭 SSE 代理缓冲。SELinux 开启时，允许 Nginx 连接上游，保留 SELinux 的正常保护：

```sh
sudo setsebool -P httpd_can_network_connect 1
sudo nginx -t
sudo systemctl enable --now nginx.service
sudo systemctl reload nginx.service
```

此布尔项用于允许 Web 服务发起上游连接，参见 [Red Hat Nginx 反向代理说明](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html-single/deploying_web_servers_and_reverse_proxies/index)。

若 `firewall-cmd --state` 显示 firewalld 正在运行，在实际网卡所在 zone 中放行 HTTP/HTTPS；下例使用默认 zone：

```sh
sudo firewall-cmd --get-active-zones
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

使用其他 zone 时，为上述放行命令增加 `--zone=实际zone`。云服务器安全组也需要允许 80/443；PostgreSQL、Redis、8000 和 8501 保持内网或本机访问。

随包模板先提供 HTTP 入口。HTTPS 可由现有负载均衡/网关终止；若由本机 Nginx 终止，应为两个域名准备有效证书，在对应 `server` 中改用 `listen 443 ssl;`，配置 `ssl_certificate`、`ssl_certificate_key`，并另设 80 端口跳转至 HTTPS。修改后执行 `sudo nginx -t` 和 `sudo systemctl reload nginx.service`。

当前网页界面没有浏览器用户登录入口，会用服务凭据访问后端；公开访问前应在网关配置访问控制。API 的 bearer 认证仍由应用执行，不要移除 `AUTH_SECRET` 或 App Token 认证。
