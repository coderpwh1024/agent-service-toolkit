# GitHub MCP 智能体

GitHub MCP 智能体是一种专用智能体，使用 GitHub MCP（模型上下文协议）工具完成仓库管理和
开发工作流。它基于 LangGraph 的 `create_react_agent` 构建，以简洁的方式实现
ReAct（推理与行动）模式。

**该智能体旨在演示如何使用 MCP（模型上下文协议）服务器和工具构建智能体。**

## 功能

配置 PAT 后，[GitHub MCP 服务器](https://github.com/github/github-mcp-server) 可提供多种工具。

- 仓库管理（创建、克隆、浏览）
- Issue 管理（创建、列出、更新、关闭）
- 拉取请求管理（创建、审查、合并）
- 分支管理（创建、切换、合并）
- 文件操作（读取、写入、搜索）
- 提交操作（创建、查看历史）

## 配置

要启用 GitHub MCP 智能体，需要配置以下环境变量：

### 必需设置

```bash
# GitHub 个人访问令牌（GitHub MCP 服务器必需）
# 如果未设置，GitHub MCP 智能体将没有可用工具
GITHUB_PAT=your_github_personal_access_token_here
```

### 可选设置

```bash
# GitHub MCP 服务器 URL（默认为 https://api.githubcopilot.com/mcp/）
MCP_GITHUB_SERVER_URL=https://api.githubcopilot.com/mcp/
```

## GitHub 个人访问令牌

要使用 GitHub MCP 智能体，需要一个具备适当权限的 GitHub 个人访问令牌（PAT）。GitHub MCP
服务器提供多种仓库管理工具，不同工具需要不同的权限范围。

1. 前往 GitHub Settings → Developer settings → Personal access tokens → Tokens (classic)
2. 生成具有以下权限范围的新令牌：
   - `repo`（完整控制私有仓库）——启用 `create_issue`、`create_pull_request`、
     `get_file_contents`、`list_commits` 等工具
   - `read:org`（读取组织和团队成员信息）——启用组织相关工具
   - `read:user`（读取用户个人资料数据）——启用用户资料工具
   - `user:email`（访问用户电子邮件地址）——启用电子邮件相关功能

**注意**：具体可用工具取决于 PAT 的权限范围。使用上述建议权限后，你将能够访问大多数
仓库管理工具，包括创建 Issue 和拉取请求、读取文件内容以及列出提交。

## 用法

配置完成后，GitHub MCP 智能体将在服务中以 `github-mcp-agent` 的名称提供。

### 示例提示词

以下是一些可用于 GitHub MCP 智能体的示例提示词：

- **“介绍 JoshuaC215/agent-service-toolkit 仓库”** —— 显示仓库信息和 README 内容
- **“列出此仓库最近的提交”** —— 显示最近的提交历史
- **“src 目录中有哪些文件？”** —— 列出指定目录中的文件
- **“显示 README 文件”** —— 显示仓库的 README 内容
- **“创建一个标题为‘Bug：登录无法使用’的新 Issue，描述为‘登录表单对用户输入没有响应’”** —— 创建新 Issue
- **“此仓库有哪些未关闭的 Issue？”** —— 列出未关闭的 Issue
- **“创建一个从 feature-branch 到 main 的拉取请求，标题为‘添加新功能’”** —— 创建拉取请求
- **“显示此仓库的信息”** —— 显示仓库详情
