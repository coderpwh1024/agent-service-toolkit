# 使用 Ollama

⚠️ _**注意：** agent-service-toolkit 对 Ollama 的支持尚处于实验阶段，实际效果可能不符合预期。
以下说明已在 MacBook Pro 上使用 Docker Desktop 测试。如遇到任何问题，请提交 Issue。_

你也可以使用 [Ollama](https://ollama.com) 运行智能体服务所依赖的 LLM。

1. 按照 <https://github.com/ollama/ollama> 中的说明安装 Ollama
1. 安装任意想使用的模型，例如执行 `ollama pull llama3.2`，并将 `OLLAMA_MODEL`
   环境变量设为要使用的模型，例如 `OLLAMA_MODEL=llama3.2`

如果在本地运行服务（例如 `python src/run_service.py`），至此应该已经可以正常使用。

如果在 Docker 中运行服务，还需要执行以下操作：

1. [按照此处说明配置 Ollama 服务器](https://github.com/ollama/ollama/blob/main/docs/faq.md#how-do-i-configure-ollama-server)，
   例如在 macOS 上运行 `launchctl setenv OLLAMA_HOST "0.0.0.0"`，然后重启 Ollama。
1. 将 `OLLAMA_BASE_URL` 环境变量设为 Ollama 服务器的基础 URL，例如
   `OLLAMA_BASE_URL=http://host.docker.internal:11434`
1. 或者，也可以在 Docker 中运行 `ollama/ollama` 镜像并采用类似配置
   （但在某些情况下速度可能较慢）。
