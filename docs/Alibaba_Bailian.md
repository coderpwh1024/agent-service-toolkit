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
