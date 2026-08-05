# 真实端到端验证（无需 API 密钥）

单元测试会模拟 LLM 和传输层，因此只有运行服务检查才能发现 FastAPI、uvicorn、
langgraph 或检查点存储器升级导致的集成回归；也只有真实浏览器测试才能发现 Streamlit
技术栈的 UI 故障。

## 伪模型 HTTP 分层验证

服务内置了一个伪模型（`USE_FAKE_MODEL=true`），它能满足启动时“至少配置一个 LLM
密钥”的检查，并返回预设回复。因此，无需提供商凭据即可通过升级后的技术栈调用真实
HTTP API。不设置 `AUTH_SECRET` 时，端点不会启用身份验证（无需 bearer 令牌）。

以原生方式运行（速度最快：会使用与生产相同的 `run_service.py`/uvicorn 入口点和完整
依赖项技术栈，只是不使用容器）：

```sh
PYTHONPATH=src USE_FAKE_MODEL=true HOST=127.0.0.1 PORT=8080 uv run python src/run_service.py &
# 等待 /health 可用后执行：
curl -s localhost:8080/health                       # {"status":"ok"}  -> 应用与生命周期已启动
curl -s localhost:8080/info                          # 列出智能体/模型
curl -s -XPOST localhost:8080/invoke   -H 'content-type: application/json' \
     -d '{"message":"hi","agent_id":"chatbot","model":"fake"}'         # -> 返回结果与 run_id
curl -s -N -XPOST localhost:8080/stream -H 'content-type: application/json' \
     -d '{"message":"hi","agent_id":"chatbot","model":"fake","stream_tokens":true}'  # SSE 令牌
# 持久化：使用相同的 "thread_id" 调用 invoke 两次，再为该线程发送 POST /history，
# 确认检查点存储器返回之前的对话轮次（用于验证检查点存储器包）。
```

各项检查的验证范围：`/health` + `/info` 验证 FastAPI/uvicorn/pydantic 启动与智能体
装配；`/invoke` 验证完整图运行（langgraph + langchain + langsmith `run_id`）；
`/stream` 验证 SSE `StreamingResponse` 路径；使用重复 `thread_id` 调用 `/history`
验证检查点存储器（默认使用 langgraph-checkpoint-sqlite/aiosqlite）。

## Streamlit UI 端到端测试（浏览器）

CI 从不驱动真实 Streamlit 界面：pytest 会模拟传输层，Docker CI 作业也只检查健康端点。
因此，只有真实浏览器测试才能发现升级引起的 Streamlit/pandas/pyarrow 层面 UI 故障。
保持上述伪模型服务运行，然后执行：

```sh
uv run streamlit run src/streamlit_app.py --server.headless true --server.port 8501 &
# 快速单消息冒烟测试：
uv run --with playwright python scripts/smoke_live_app.py http://localhost:8501
# 更全面的覆盖（多轮对话恢复、设置选择器、反馈组件、流式传输开关等）：
uv run --with playwright python scripts/e2e_ui_tests.py http://localhost:8501
```

`smoke_live_app.py` 会发送一条聊天消息，并验证流式响应能够渲染且最终稳定。
`e2e_ui_tests.py` 是一组基于相同思路构建的关键用户旅程小型测试套件。运行 `--list`
可查看测试名称，也可以传入名称仅运行其中一部分。两者通过时均以状态码 0 退出，失败时
保存诊断截图；两者都支持 URL 参数，因此同一命令也能针对已部署应用运行（省略 URL，
或设置 `LIVE_APP_URL`）。凡是提升 `streamlit`、其渲染技术栈（pandas、pyarrow、
pillow、altair）或 UI 使用的客户端/数据模式代码版本，在打开 PR 前至少运行冒烟测试；
升级可能影响聊天记录、设置、反馈或流式传输路径，则运行完整测试套件。

## 容器化测试

要验证镜像本身（基础镜像 + 镜像内的 `uv sync`），请运行
`docker compose up --build`，并通过映射端口访问相同端点。这与 `test-docker` CI
作业一致。该测试需要拉取 Python slim 基础镜像，因此需要访问 Docker Hub 的出站网络
权限（并非所有沙箱都具备；CI 通过 Docker-in-Docker 处理）。
