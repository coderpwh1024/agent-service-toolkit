# 使用 Google 模型

Google 提供两种不同的模型访问方式，它们分别受不同的使用条款和使用政策约束。

选择哪种方式超出了本文档的范围，但你需要了解两者之间的差异。

可选方式如下：

1. Gemini Developer API：[文档链接](https://ai.google.dev/gemini-api/docs)
2. Google Cloud Platform 上的 Google Vertex AI：[文档链接](https://cloud.google.com/vertex-ai/docs)

## 使用 Gemini Developer API

[从 Google 获取 Gemini API 密钥](https://ai.google.dev/gemini-api/docs)，即可在 Agent Service
Toolkit 中快速开始使用。

1. 将 API 密钥写入 `.env` 文件中的 `GOOGLE_API_KEY` 环境变量
2. Agent Service Toolkit 应该能够检测到该凭据，随后即可开始使用

## 使用 Google Cloud Platform 上的 Google Vertex AI

### 前提条件

确保你拥有一个已[启用结算功能](https://console.cloud.google.com/billing)的
[Google Cloud 项目](https://console.cloud.google.com/projectcreate)。

### 身份验证说明

要以编程方式使用 Vertex AI，需要创建一个**服务账号**，并使用其凭据对应用进行身份验证。
这些凭据不同于你的个人 Google 账号凭据，它们决定应用对 Google Cloud 服务和 API 的访问权限。

Vertex 使用基于 JSON 的凭据文件，并在运行时读取 `GOOGLE_APPLICATION_CREDENTIALS`
环境变量，以获取该凭据文件的路径。

### 模型

Vertex AI 同时提供**稳定版**和**实验版/预览版**模型。实验版和预览版模型可能随时更改或停止提供，
恕不另行通知，因此强烈建议**生产应用**使用稳定版模型。请查看
[Vertex AI 文档](https://cloud.google.com/vertex-ai/docs)，了解最新的模型状态。

### 操作步骤

#### 1. 启用 Vertex AI API

- 前往 [Google Cloud API Library](https://console.cloud.google.com/apis/library)。
- 从页面顶部的下拉菜单中选择项目。
- 搜索“Vertex AI API”，然后点击 **Enable**。

#### 2. 创建并配置服务账号

- 前往[凭据页面](https://console.cloud.google.com/apis/credentials)。
- 点击 **Create Credentials** > **Service Account**。
- 填写详细信息（例如名称和描述）。
- **分配角色**：对于 Vertex AI，至少授予“Vertex AI User”角色。
- 点击 **Done**，找到你的服务账号，点击三个点（⋮），然后选择 **Manage Keys**。
- 点击 **Add Key** > **Create New Key**，选择 **JSON**，然后点击 **Create**。
- JSON 密钥文件将自动下载。请**妥善保管**，之后无法再次下载该文件。

#### 3. 将 JSON 密钥文件添加到[基于文件的凭据](docs/File_Based_Credentials.md)路径

将下载的 JSON 文件放入项目的 `privatecredentials/` 目录
（例如 `privatecredentials/service-account-key.json`）。

[基于文件的凭据](docs/File_Based_Credentials.md)路径中的内容会在运行时通过
`/privatecredentials/` 提供给容器，但不会包含在 Git 提交和 Docker 构建中。

#### 4. 设置 `GOOGLE_APPLICATION_CREDENTIALS` 环境变量

将 `GOOGLE_APPLICATION_CREDENTIALS` 环境变量设为 JSON 文件的**完整路径**：

- **.env**（用于 Docker Compose）：

    ```env
    GOOGLE_APPLICATION_CREDENTIALS=/privatecredentials/service-account-key.json
    ```

- **类 Unix 系统**：

    ```bash
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your/project/privatecredentials/service-account-key.json
    ```

- **Windows**：

    ```cmd
    set GOOGLE_APPLICATION_CREDENTIALS=C:\path\to\your\project\privatecredentials\service-account-key.json
    ```

#### 5. 保护凭据

- 确保 `.gitignore` 文件涵盖该 JSON 文件（如果已将其放入项目提供的
  `privatecredentials/` 文件夹，则此项已完成）：

  ```text
  service-account-key.json
  ```

- 请**对该文件严格保密**，因为它能够访问你的 Google Cloud 资源，并可能导致**未经授权的使用**
  或产生费用。

### 验证设置

使用以下命令测试凭据：

  ```bash
  gcloud auth activate-service-account --key-file=/path/to/your/service-account-key.json
  gcloud auth list
  ```

  你的服务账号应显示为活动状态。

### 生产环境说明

此设置非常适合开发环境。在生产环境中，请考虑采用更安全的替代方案。
[基于文件的凭据](docs/File_Based_Credentials.md)页面列出了一些可选方案。
