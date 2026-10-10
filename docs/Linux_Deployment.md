# 单台 Linux 服务器部署

本文说明如何将当前仓库构建为 Docker 镜像，并在一台 Linux 服务器上运行 FastAPI、Streamlit、PostgreSQL 和 Redis。示例以 Ubuntu、Docker Compose、百炼模型和独立部署为准。命令中的域名、仓库地址、用户名及密钥都需要替换。

## 部署结构

```text
浏览器 ──HTTPS──> Nginx ──> 127.0.0.1:8501 ──> Streamlit
移动端/API 客户端 ──HTTPS/WSS──> Nginx ──> 127.0.0.1:8000 ──> FastAPI
                                                       │
                                            Compose 内网：PostgreSQL、Redis
```

仓库已有两个应用镜像定义：`docker/Dockerfile.service` 和 `docker/Dockerfile.app`。`compose.yaml` 负责运行四个服务，`postgres_data` 和 `redis_data` 卷保存数据。正式运行使用 `docker compose up -d --build`；`docker compose watch` 是开发时的文件监视模式。

### 当前配置需要注意的地方

- `config/environments/test.env` 设置 `MODE=prod`，但同时设置 `PORT=8000`；`compose.yaml` 却将宿主机端口转发到容器的 `8080`，健康检查也访问 `8080`。下文的覆盖文件会明确设置容器内 `PORT=8080`。
- 原始 Compose 文件公开发布 PostgreSQL、Redis、FastAPI 和 Streamlit 的端口。下文的覆盖文件取消前两者的发布，并将后两者限定在服务器本机。
- 应用的健康检查使用 `curl`，但两个 Dockerfile 没有显式安装 `curl`。下文改用镜像中已有的 Python 标准库进行探活。
- 当前 `APP_ENV` 只支持 `local` 和 `test`。本示例使用 `APP_ENV=test` 取得 `MODE=prod`，并通过覆盖文件关闭 Nacos。`test` 是当前代码中的配置选择名，不能把仓库内的共享测试 Nacos 地址当作生产配置。

## 1. 准备服务器和代码

先准备一台可通过 SSH 登录的 Linux 服务器及两个解析到该服务器的域名，例如 `app.example.com` 和 `api.example.com`。安全组允许 SSH、80 和 443；不需要对公网开放 5432、6379、8000 或 8501。内存和磁盘容量应根据模型客户端、语音功能及 PostgreSQL 数据量实际评估。

Ubuntu 上按 [Docker 官方说明](https://docs.docker.com/engine/install/ubuntu/)添加 Docker 的软件源，再安装 Docker Engine、Buildx 和 Compose 插件。运行 `docker compose version`，确认 Compose 至少为 **2.24.4**，因为下文使用了 `!override` 标记。

将准备部署的本地代码提交并推送到自己的仓库，在服务器上克隆指定版本：

```sh
mkdir -p ~/apps
cd ~/apps
git clone YOUR_REPOSITORY_URL agent-service-toolkit
cd agent-service-toolkit
git checkout RELEASE_TAG_OR_COMMIT
```

Git 不会传送被忽略的 `.env`、私有凭据和运行产物。若没有可供服务器访问的 Git 仓库，可以在本地运行 `git archive --format=tar HEAD > agent-service-toolkit.tar`，将该 tar 文件传到服务器后解包；`git archive HEAD` 同样只包含已提交的内容。不要把 `.env` 或 `privatecredentials/` 放入代码包。

## 2. 配置运行密钥

在服务器的项目根目录创建 `.env`。下面的值仅作字段示例，不可原样使用：

```dotenv
APP_ENV=test
POSTGRES_USER=agent_service
POSTGRES_DB=agent_service
POSTGRES_PASSWORD=replace-with-a-unique-strong-password
AUTH_SECRET=replace-with-a-long-random-service-secret
DASHSCOPE_API_KEY=replace-with-your-model-api-key
```

```sh
chmod 600 .env
```

`POSTGRES_PASSWORD` 同时用于初始化 Compose 中的数据库和配置服务端连接；`AUTH_SECRET` 是 Streamlit 调用 API 使用的可信服务凭据。至少需要一种可用模型凭据，本例使用 `DASHSCOPE_API_KEY`。不要把真实值提交到 Git、放入命令行参数或贴到工单中。已有 PostgreSQL 数据卷时，修改初始化密码不会自动修改库内已有用户密码，需按数据库变更流程处理。

本示例关闭 Nacos，使用 `.env` 和 Compose 提供模型、认证、数据库和 Redis 配置。若部署必须使用 Nacos，请先在独立的部署环境准备 Nacos 配置，并确保其 PostgreSQL、Redis 地址与凭据可从服务容器访问；远程配置会覆盖本地同名字段，且启用强制存储配置时缺失字段会导致启动失败。详见[环境配置说明](Environment_Configuration.md)。

## 3. 创建生产覆盖文件

在项目根目录创建 `compose.prod.yaml`：

```yaml
services:
  postgres:
    ports: !override []
    restart: unless-stopped

  redis:
    ports: !override []
    restart: unless-stopped

  agent_service:
    environment:
      MODE: prod
      PORT: "8080"
      NACOS_ENABLED: "false"
    ports: !override
      - "127.0.0.1:8000:8080"
    volumes: !override []
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  streamlit_app:
    ports: !override
      - "127.0.0.1:8501:8501"
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/healthz', timeout=3)"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
```

`!override` 会替换原始端口列表；普通 Compose 合并可能把新端口追加到原列表，造成数据库等端口仍被公开。覆盖文件同时移除了开发用的 `privatecredentials/` 目录挂载。确实需要文件凭据时，应单独配置受限的只读挂载。参见 [Compose 合并规则](https://docs.docker.com/reference/compose-file/merge/)及 [Docker 端口发布说明](https://docs.docker.com/engine/network/port-publishing/)。

## 4. 构建、启动和检查

在服务器的项目根目录执行：

```sh
docker compose -f compose.yaml -f compose.prod.yaml config -q
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
docker compose -f compose.yaml -f compose.prod.yaml ps
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8501/healthz
```

`config -q` 只验证合并后的配置，不输出可能包含密钥的完整配置。启动失败时查看相关服务的近期日志：

```sh
docker compose -f compose.yaml -f compose.prod.yaml logs --tail=100 agent_service streamlit_app
```

不要直接把日志贴到公开渠道。服务启动依赖 PostgreSQL；如果模型凭据无效，健康检查通过也不代表实际模型调用成功。可以在访问受控的前提下做一次真实调用验证，但它可能产生模型费用。

## 5. 接入 HTTPS 反向代理

以下 Nginx 配置展示两个域名的 HTTP 入口。先把域名解析到服务器，再将内容放入服务器的 Nginx 站点配置；随后用 Certbot 添加 HTTPS：

```nginx
server {
    listen 80;
    server_name app.example.com;

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}

server {
    listen 80;
    server_name api.example.com;
    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

验证 Nginx 配置后重新加载，再按 [Certbot 的 Nginx 安装说明](https://certbot.eff.org/instructions?os=ubuntufocal&ws=nginx)安装 Certbot、申请证书并检查自动续期：

```sh
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d app.example.com -d api.example.com
sudo certbot renew --dry-run
```

Streamlit 和语音使用 WebSocket，代理必须传递 Upgrade 头；API 流式响应需要关闭代理缓冲。参见 [Nginx WebSocket 文档](https://nginx.org/en/docs/http/websocket.html)和 [Nginx 反向代理文档](https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy)。

**访问控制：** 当前 Streamlit 界面使用服务级 `AUTH_SECRET` 访问后端，尚无面向浏览器用户的登录流程。若公开 Streamlit 域名，访问者都可以通过该界面使用服务能力。上线时应由网关限制访问，或先完成面向用户的登录与授权。API 本身也应始终配置 `AUTH_SECRET` 或 App Token 密钥，不能以无认证模式公开。

## 6. 更新与备份

发布新版本前备份 PostgreSQL。示例会在服务器当前目录生成备份文件，应将其转存到受控的备份位置：

```sh
docker compose -f compose.yaml -f compose.prod.yaml exec -T postgres \
  pg_dump -U agent_service -d agent_service -Fc > agent-service-toolkit.dump
```

若通过 Git 部署，拉取并切换到经过验证的提交后重新构建：

```sh
git fetch --tags
git checkout NEW_RELEASE_TAG_OR_COMMIT
docker compose -f compose.yaml -f compose.prod.yaml config -q
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
docker compose -f compose.yaml -f compose.prod.yaml ps
```

部署时保留同一 Compose 项目名和数据卷，数据库与 Redis 数据会继续保存在原卷中。不要在正常更新流程中使用 `docker compose down -v`；它会删除这些数据卷。修改 Nacos、环境文件或服务镜像后，应重新创建受影响的应用容器。

## 可选：在本地先打包镜像

如果服务器不方便编译 Python 依赖，可以在有 Docker Buildx 的机器上按服务器 CPU 架构分别构建两个镜像，再推送到镜像仓库或使用 `docker save`/`docker load` 传输。Mac Apple Silicon 构建常见 x86_64 Linux 服务器镜像时，应明确指定 `--platform linux/amd64`；ARM 服务器则改为 `linux/arm64`。示例：

```sh
docker buildx build --platform linux/amd64 --load \
  -t agent-service-toolkit-service:release-1 -f docker/Dockerfile.service .
docker buildx build --platform linux/amd64 --load \
  -t agent-service-toolkit-app:release-1 -f docker/Dockerfile.app .
docker save -o agent-service-toolkit-images.tar \
  agent-service-toolkit-service:release-1 agent-service-toolkit-app:release-1
```

服务器运行 `docker load -i agent-service-toolkit-images.tar` 后，还需在生产覆盖文件中分别为 `agent_service` 和 `streamlit_app` 指定上述 `image` 标签，并用 `docker compose ... up -d --no-build` 启动。服务器仍需取得 PostgreSQL 和 Redis 镜像，且需要部署配置文件及独立创建的 `.env`。跨架构构建及镜像传输参见 [Docker 多平台构建文档](https://docs.docker.com/build/building/multi-platform/)。
