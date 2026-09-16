# 基于文件的凭据

在开发智能体时，你可能会发现有些凭据需要存储在磁盘上，但不希望将它们存入 Git
仓库或打包进容器镜像。

例如：

- 基于文件的 LLM 凭据文件（例如 Google Vertex）
- 与外部 API 通信所需的证书或私钥

`privatecredentials/` 文件夹为你提供了一个方便的位置，可在开发环境中存放这些文件。

## 工作原理

### 保护机制

- `.dockerignore` 文件会排除整个文件夹，防止其进入构建流程。
- `.gitignore` 文件只允许 `.gitkeep` 文件，因为 Git 不会追踪空文件夹。

### 挂载卷

Docker Compose 文件会将 `privatecredentials/` 挂载到容器内的
`/privatecredentials/`。运行中的容器可以访问开发环境里这些未被追踪的文件。

### 为什么不使用 Docker Watch

未使用 Docker Watch 的同步功能，原因如下：

- Docker Watch 遵循 `.dockerignore` 中的规则，因此无法发现这些凭据
- 即使能够发现，Docker Watch 也不会在容器启动时执行初始同步，只会同步服务运行期间发生的更改

## 建议用法

对于每个基于文件的凭据，请执行以下操作：

1. 将文件（例如 `example-creds.txt`）放入 `privatecredentials/` 文件夹
2. 在 `.env` 文件中为该凭据创建一个环境变量（例如
   `EXAMPLE_CREDENTIAL=/privatecredentials/example-creds.txt`），供智能体在运行时引用其位置
3. 在智能体中，凡是需要凭据路径的地方都使用该环境变量

### 示例

#### Google Vertex

Google Vertex SDK 使用 `GOOGLE_APPLICATION_CREDENTIALS` 环境变量定位凭据文件。

请执行以下操作：

1. 将 `service-account-key.json`（或 `google-credentials.json`）放入
   `privatecredentials/` 文件夹
2. 在 `.env` 文件中定义
   `GOOGLE_APPLICATION_CREDENTIALS=/privatecredentials/service-account-key.json`
3. Vertex SDK 会自动读取 `GOOGLE_APPLICATION_CREDENTIALS` 环境变量。

#### 用于与远程 API 进行签名通信的证书

如果智能体调用的远程 API 要求客户端证书，智能体必须能够访问该公共证书。

例如，假设你有一个名为 `my_remote_api_certificate.cer` 的证书。

请执行以下操作：

1. 将 `my_remote_api_certificate.cer` 放入 `privatecredentials/` 文件夹
2. 在 `.env` 文件中定义
   `MY_REMOTE_API_CERTIFICATE=/privatecredentials/my_remote_api_certificate.cer`
3. 让智能体中的 HTTP 客户端通过该环境变量值访问文件

## 生产环境方案

在生产环境中，你需要让应用能够访问基于文件的凭据，并通过环境变量指定容器可访问
这些凭据的位置。

可采用多种方式：

- 使用以数据卷形式挂载的 Kubernetes Secrets 或 Docker Secrets，让应用能够以文件形式访问它们
- 使用云托管环境的密钥管理功能（Google Cloud Secrets、AWS Secrets Manager 等）
- 使用第三方密钥管理平台
- 手动将凭据放到 Docker 主机上，再通过挂载卷将其映射到容器中（安全性较低）
