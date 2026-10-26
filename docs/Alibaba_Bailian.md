# 使用阿里云百炼千问

项目默认支持阿里云百炼的千问 3.8 Max，模型 ID 为 `qwen3.8-max`。百炼通过 OpenAI 兼容的 Chat API 接入，因此不需要额外安装 SDK。

RAG 使用 `qwen3.7-text-embedding` 生成向量，与聊天和语音复用同一个账号。

## 配置

项目会直接读取启动进程环境中的 `DASHSCOPE_API_KEY`。如果已经在 macOS 的 shell 配置中导出该变量，直接运行 Python 服务即可，无需在项目中创建 `.env`。Docker Compose 也会将启动它的宿主机环境中的百炼变量传入服务容器。

可以用下面的命令只检查变量是否存在；该命令不会显示密钥值：

```sh
if [ -n "${DASHSCOPE_API_KEY:-}" ]; then echo "DASHSCOPE_API_KEY 已配置"; else echo "DASHSCOPE_API_KEY 未配置"; fi
```

项目根目录的 `.env` 仍然受支持，但它是可选的。需要使用 `.env` 时配置：

```env
DASHSCOPE_API_KEY=sk-your-bailian-api-key
```

默认访问中国大陆（北京）地域的兼容地址：

```env
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

语音合成使用百炼原生接口，默认地址为：

```env
DASHSCOPE_NATIVE_BASE_URL=https://dashscope.aliyuncs.com/api/v1
```

阿里云建议新项目使用业务空间专属地址。可以在百炼控制台查看业务空间 ID，并根据所在地域覆盖 `DASHSCOPE_BASE_URL`，例如：

```env
DASHSCOPE_BASE_URL=https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
```

API Key 必须与访问地址属于同一地域。其他地域地址请参考[阿里云百炼 OpenAI 兼容接口文档](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)。

## 默认模型

配置 `DASHSCOPE_API_KEY` 后，`qwen3.8-max` 会成为服务默认模型。即使同时配置了其他模型提供商，千问仍会保持默认；请求可以继续通过 `model` 参数选择 `/info` 返回的其他可用模型。

如需显式指定，也可以配置：

```env
DEFAULT_MODEL=qwen3.8-max
DASHSCOPE_EMBEDDING_MODEL=qwen3.7-text-embedding
```

## 语音输入与输出

语音识别和语音合成都复用同一个 `DASHSCOPE_API_KEY`，不再需要
`OPENAI_API_KEY`、`DEEPGRAM_API_KEY` 或 `ELEVENLABS_API_KEY` 等额外语音账号。
当 Streamlit 进程能读取到 `DASHSCOPE_API_KEY` 且语音 Provider 变量未设置时，
项目会自动启用百炼语音功能。

默认模型与音色如下：

```env
VOICE_STT_PROVIDER=alibaba
VOICE_STT_MODEL=qwen3-asr-flash
VOICE_TTS_PROVIDER=alibaba
VOICE_TTS_MODEL=qwen3-tts-flash
VOICE_TTS_VOICE=Cherry
VOICE_TTS_LANGUAGE=Auto
```

`qwen3-asr-flash` 负责语音识别，`qwen3-tts-flash` 负责语音合成。聊天输入框右侧的
麦克风用于录音；设置面板中的 “Enable audio generation” 控制回答语音。若只想关闭
其中一个方向，把相应的 `VOICE_STT_PROVIDER` 或 `VOICE_TTS_PROVIDER` 设为空值即可。

接口细节可参考阿里云官方的
[千问语音识别 API](https://help.aliyun.com/zh/model-studio/qwen-asr-api-reference) 和
[千问语音合成 API](https://help.aliyun.com/zh/model-studio/qwen-tts-api)。

## 流式用量与显式缓存

普通聊天入口显式应用 `models.chat.enable_thinking`（环境变量 `CHAT_LLM_ENABLE_THINKING`，默认 `false`），请求可用 `agent_config.enable_thinking=true` 开启深度推理。语音仍用既有独立开关。

阿里云聊天客户端启用 `stream_usage=True`，用于读取流式末帧 Token 用量，不影响正文流式返回。主对话的模型阶段、输入 Token 和缓存命中量由 `core.model_metrics` 记录。

`models.dashscope.explicit_cache_enabled`（环境变量 `DASHSCOPE_EXPLICIT_CACHE_ENABLED`）默认关闭。启用后，主对话只在固定 System 指令末端添加供应商支持的显式缓存断点。动态状态、检索记忆和用户问题保持在断点之后。显式缓存有最低前缀长度、有效期和创建费用要求，命中比例不能直接换算为首 Token 加速比例，应先对照实测。

- [百炼上下文缓存](https://help.aliyun.com/zh/model-studio/context-cache)
- [显式缓存最佳实践](https://help.aliyun.com/zh/model-studio/explicit-cache-guide)
- [流式输出与用量](https://help.aliyun.com/zh/model-studio/stream)
