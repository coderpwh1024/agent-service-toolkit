# 本地与测试环境配置说明

## 目标与结论

项目现已区分 `local`（本地开发）和 `test`（测试）两个运行环境。切换环境只需修改根目录 `.env` 中的 `APP_ENV`，不需要复制、重命名或手工覆盖公共配置。

```dotenv
# 本地开发环境
APP_ENV=local

# 测试环境
APP_ENV=test
```

也可以只对当前命令临时切换，进程环境变量的优先级最高：

```sh
APP_ENV=test uv run python src/run_service.py
```

## 配置关系总览

整套配置分为五层，各层职责不同：

```text
根 .env：选择 local/test，提供 Compose 宿主机端口
  │
  ├─ APP_ENV=local
  │    ├─ config/environments/local.env          公共、本机运行配置
  │    ├─ .env.local                              私密、本机覆盖配置
  │    └─ config/environments/local.compose.env  仅 Compose 的地址覆盖
  │
  └─ APP_ENV=test
       ├─ config/environments/test.env           公共、测试环境配置
       ├─ .env.test                               私密、测试覆盖配置
       └─ config/environments/test.compose.env   仅 Compose 的地址覆盖

服务启动后：从所选 Nacos 的 agent-service-toolkit.yaml 加载业务配置
```

`.env.example`、`.env.local.example` 和 `.env.test.example` 只是可提交的安全模板，运行时不会读取任何 `*.example` 文件。它们与实际文件的对应关系如下：

| 安全模板 | 复制后得到的实际文件 | 运行时作用 |
| --- | --- | --- |
| `.env.example` | `.env` | 选择 `APP_ENV`，设置 `AGENT_HOST_PORT` |
| `.env.local.example` | `.env.local` | 保存本地 Nacos 用户名、密码等私密覆盖值 |
| `.env.test.example` | `.env.test` | 保存测试 Nacos 用户名、密码等私密覆盖值 |

实际 `.env*` 文件被 Git 和 Docker 构建忽略；三个 `*.example` 文件不含真实密码，应提交到版本库。修改 example 不会自动同步到已有实际文件，反过来也一样。

### `.env.test` 与 `.env.test.example`

- `.env.test.example` 是团队共享模板，只能放占位密码和说明。
- `.env.test` 是当前机器的真实测试环境私密配置，由模板复制后填写真实凭据。
- 只有 `APP_ENV=test` 时才加载 `.env.test`；`APP_ENV=local` 时不会加载它。
- `.env.test` 可以覆盖 `test.env` 中的同名字段，但建议只保存用户名、密码、服务注册地址等不可提交或机器相关的值。

### `.env.local` 与 `.env.local.example`

- `.env.local.example` 是团队共享模板，用途与测试模板相同，但面向本地 Nacos。
- `.env.local` 是当前机器的真实本地私密配置。
- 只有 `APP_ENV=local` 时才加载 `.env.local`；切换到测试环境后不会继续混入本地配置。
- 当前工作区的 `.env.local` 由原有 `.env` 原样迁移而来，以避免丢失既有凭据。后续可将其中内容继续精简为本地私密覆盖项。

### `config/environments/local.env` 与 `test.env`

这两个文件是可提交的环境公共配置，包含运行模式、Nacos SDK 地址、控制台地址、Namespace、Group、Data ID、服务名等非敏感引导字段：

- `local.env`：本地 Python 进程连接 `127.0.0.1:8848`，运行模式为 `dev`；应用监听 `8000`，避免与本地 Nacos 控制台的 `8080` 冲突。
- `test.env`：连接测试 Nacos `124.221.238.140:8848`，运行模式为 `prod`。
- 两个文件都不得保存真实密码、令牌或业务数据库凭据。
- 启动时只会加载与 `APP_ENV` 对应的一个文件，不会同时加载 `local.env` 和 `test.env`。

### `local.compose.env` 与 `test.compose.env`

这两个文件只在 Docker Compose 启动时追加加载，用来处理“容器看到的网络地址”和“宿主机进程看到的地址”不同的问题：

- `local.compose.env` 把 `NACOS_SERVER_ADDR` 从宿主机进程使用的 `127.0.0.1:8848` 转换为容器使用的 `host.docker.internal:8848`。容器内的 `127.0.0.1` 指向容器自身，不能代表宿主机。
- `test.compose.env` 保持 `124.221.238.140:8848`。虽然当前值与 `test.env` 相同，但独立保留后，未来测试环境的容器入口与本机入口不同时无需修改公共配置语义。
- 直接用 Python 启动时不读取 `*.compose.env`。
- 这两个文件同样只能保存非敏感容器覆盖值。

## 环境地址

| 环境 | `APP_ENV` | Nacos SDK 地址 | Nacos 控制台 | 运行模式 |
| --- | --- | --- | --- | --- |
| 本地开发 | `local` | `127.0.0.1:8848` | `http://127.0.0.1:8080/` | `dev` |
| 测试 | `test` | `124.221.238.140:8848` | `http://124.221.238.140:8080/` | `prod` |

Nacos 3.x 的客户端服务端口是 `8848`，对应的 gRPC 端口是 `9848`；`8080` 仅用于浏览器访问控制台，不能配置到 `NACOS_SERVER_ADDR`。本次检查中，测试环境控制台根地址返回 HTTP 302，`8848` 和 `9848` 的 TCP 端口均可连通；这只能证明网络入口可达，不代表账号登录、Data ID 内容或后端数据库已验证。

用户给出的测试账号用户名为 `nacos`。密码属于秘密，不写入版本库或本文档，应放入被 Git 忽略的 `.env.test`。

## 文件职责

| 文件 | 是否提交 | 用途 |
| --- | --- | --- |
| `.env` | 否 | 选择 `APP_ENV`；兼容既有本地覆盖值 |
| `config/environments/local.env` | 是 | 本地环境的非敏感 Nacos 引导配置 |
| `config/environments/test.env` | 是 | 测试环境的非敏感 Nacos 引导配置 |
| `config/environments/*.compose.env` | 是 | 容器内地址覆盖；本地 Nacos 使用 `host.docker.internal` |
| `.env.local` | 否 | 本地环境用户名、密码和注册地址等私密覆盖项 |
| `.env.test` | 否 | 测试环境用户名、密码和注册地址等私密覆盖项 |
| `.env.local.example`、`.env.test.example` | 是 | 私密覆盖文件的安全模板，不含真实密码 |
| Nacos `agent-service-toolkit.yaml` | 外部配置中心 | 模型、认证、Redis、PostgreSQL、SMTP、RAG、语音等应用配置 |

## 环境选择过程

程序先决定要使用哪个环境，再加载该环境的具体配置：

1. 如果当前进程显式设置了 `APP_ENV`，使用进程值。
2. 否则读取根 `.env` 中的 `APP_ENV`。
3. 如果两处都没有设置，默认使用 `local`。
4. 只接受 `local` 和 `test`；其他值会在启动时明确报错。

因此，临时命令 `APP_ENV=test uv run python src/run_service.py` 会覆盖 `.env` 中的 `APP_ENV=local`，但不会修改 `.env` 文件。

## 本地 Python 加载顺序

本地运行 `uv run python src/run_service.py` 时，优先级从低到高为：

```text
1. Settings 代码默认值
2. 根 .env
3. config/environments/<APP_ENV>.env
4. .env.<APP_ENV>
5. 当前进程环境变量
6. Nacos agent-service-toolkit.yaml 中允许远程管理的业务配置
```

后加载的同名值覆盖先加载的值。例如选择 `test` 后，加载的是 `.env` → `test.env` → `.env.test`；不会加载 `local.env`、`.env.local` 或任何 `*.compose.env`。

Nacos YAML 最后加载模型、认证、Redis、PostgreSQL、SMTP、RAG 和语音等业务配置，但禁止覆盖 `APP_ENV`、全部 `NACOS_*`、`HOST`、`PORT`、`MODE`、`LOG_LEVEL` 和 `GRACEFUL_SHUTDOWN_TIMEOUT` 等引导字段。

## Docker Compose 加载顺序

Compose 有两个相关阶段：

1. **Compose 文件变量插值**：当前 Shell 环境优先于根 `.env`，用于解析 `${APP_ENV}`、`${AGENT_HOST_PORT}` 等表达式。
2. **传入容器的环境变量**：`env_file` 按下面顺序加载，后面的文件覆盖前面的同名值。

```text
1. 根 .env
2. config/environments/<APP_ENV>.env
3. .env.<APP_ENV>
4. config/environments/<APP_ENV>.compose.env
5. compose.yaml 中 environment: 显式设置的字段
6. docker compose run -e 或其他显式进程覆盖
```

例如 `APP_ENV=local` 时，Compose 最后通过 `local.compose.env` 把 Nacos 地址固定为 `host.docker.internal:8848`，即使迁移后的 `.env.local` 中仍保留旧的 `127.0.0.1:8848`，容器也不会错误连接自身。

`compose.yaml` 的 `environment:` 对其明确列出的字段优先于所有 `env_file`。当前 Nacos 引导字段没有在该区块重复声明，因此由上述四层环境文件决定；PostgreSQL 容器地址等字段则由 Compose 显式设置。

## 首次配置

先创建环境选择文件：

```sh
cp .env.example .env
```

再为需要使用的环境创建私密配置：

```sh
cp .env.local.example .env.local
cp .env.test.example .env.test
```

编辑对应文件，将 `NACOS_PASSWORD` 的占位值替换为真实密码。测试环境使用任务中提供的密码，但不要把它复制进 Markdown、提交记录、日志或聊天截图。没有配置用户名和密码时，客户端会按无认证方式连接；只配置其中一个会在启动时明确失败。

对于已有工作区，可以把旧 `.env` 原样迁移为 `.env.local`，再将根 `.env` 精简为：

```dotenv
APP_ENV=local
AGENT_HOST_PORT=8000
AGENT_URL=http://127.0.0.1:8000
```

本地启动：

```sh
# .env 中设置 APP_ENV=local
uv run python src/run_service.py
```

切换到测试环境：

```sh
# .env 中设置 APP_ENV=test
uv run python src/run_service.py
```

切换后必须重启服务。当前实现只在进程启动时读取环境配置和 Nacos YAML，不支持运行时热切换。

## Docker Compose

Compose 会读取相同的 `APP_ENV`，并按环境装载公共配置、容器专用地址和私密配置：

```sh
docker compose config
docker compose watch
```

本地 Python 进程访问本机 Nacos 时使用 `127.0.0.1:8848`；`agent_service` 容器访问宿主机 Nacos 时自动改用 `host.docker.internal:8848`。测试环境无论本地进程还是容器都连接 `124.221.238.140:8848`。

如果本机 Nacos 控制台已经占用宿主机 `8080`，Compose 的 `agent_service` 默认端口会与其冲突。`.env.example` 已设置 `AGENT_HOST_PORT=8000`，Compose 会将宿主机 `8000` 映射到容器内应用的 `8080`；仅用 Python 启动服务时可通过进程环境变量设置 `PORT=8000`。不要把应用监听端口和 Nacos 控制台端口理解成同一个服务。

## Nacos 中仍需确认的内容

两个 Nacos 环境都应存在以下配置：

- Namespace ID：空字符串表示 `public`；如果测试环境使用独立命名空间，应在 `test.env` 中填写真实 Namespace ID。
- Group：`DEFAULT_GROUP`。
- Data ID：`agent-service-toolkit.yaml`，类型为 YAML。
- 应用配置：至少包含可用模型 Provider；当前 `NACOS_STORAGE_CONFIG_REQUIRED=true`，因此还必须包含完整 Redis 和 PostgreSQL 字段。
- 网络：应用运行位置必须能访问 Nacos 的 `8848` 和 `9848`；浏览器只需访问控制台 `8080`。
- 服务注册：生产式测试部署应在 `.env.test` 中显式设置调用方可达的 `NACOS_SERVICE_IP` 和 `NACOS_SERVICE_PORT`，避免注册容器私网 IP。

## 安全与排障

- `.gitignore` 和 `.dockerignore` 已覆盖 `.env`、`.env.local`、`.env.test`、运行缓存、日志和私有凭据目录；示例文件仍会纳入版本控制。
- 不要用 `http://...:8080` 作为 `NACOS_SERVER_ADDR`。SDK 字段只接受服务地址 `host:8848`，不含 `http://` 和 `/nacos`。
- 启动时报 `NACOS_USERNAME and NACOS_PASSWORD must be configured together`，说明私密文件只填写了一项。
- 能打开控制台但应用无法启动时，继续检查 `9848`、命名空间、Group、Data ID、配置内容和账号权限。
- 测试环境共用凭据已经出现在任务上下文中，建议在完成配置后按团队安全制度轮换，并限制 Nacos 控制台和客户端端口的公网访问来源。
