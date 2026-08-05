---
name: smoke-test
description: >-
  运行并解读 scripts/smoke_test.sh，按需检查 CI 和默认 docker compose 未覆盖的
  可选依赖项：Postgres 和 MongoDB 检查点存储器、AG-UI 端点以及 LangFuse 追踪。
  当改动涉及内存/检查点存储层、服务启动/健康检查、AG-UI 适配器、LangFuse 追踪，
  或提升了其中某项依赖的版本，并希望无需等待完整 CI 即可确认真实集成仍正常工作时使用。
  在判断某项变更是否有必要执行此类检查时也应使用。
---

# 可选依赖项冒烟测试

`scripts/smoke_test.sh` 用于测试本项目中未纳入快速单元测试套件或默认 CI 路径的部分，
因为它们需要真实的基础设施：**Postgres** 和 **MongoDB** 检查点存储器、**AG-UI**
端点以及 **LangFuse** 追踪。脚本会在 Docker 中启动真实依赖项，让服务连接该依赖项，
执行端到端集成验证，然后清理所有资源。

这里的“冒烟测试”是指快速确认*是否存在明显故障*，而非详尽的集成测试覆盖。
脚本特意将单个目标的运行成本控制得很低，其余目标可以跳过。

## 首先：是否确实需要运行？

大多数变更都**不需要**运行。脚本会拉取镜像、启动容器并耗时数分钟；对于未触及这些
路径的工作，不值得付出相应的时间和 token 成本。以下情况应跳过：新增或编辑智能体图、
提示词或工具；文档变更；仅涉及客户端/Streamlit 的变更；已由 `uv run pytest` 和常规
CI 覆盖的任何内容。

**当变更触及相应路径时，运行对应目标：**

| 变更内容 | 运行目标 |
| --- | --- |
| `src/memory/*`、`initialize_database`、存储器类或检查点存储器接线 | `postgres` 和/或 `mongo` |
| `langgraph-checkpoint-postgres` / `-mongodb`、`pymongo`、`psycopg` 版本 | `postgres` / `mongo` |
| `src/service/agui.py`、AG-UI 适配器或 `ag-ui-*` 依赖项 | `agui` |
| LangFuse 追踪（`CallbackHandler` 接线、`/health` 认证检查）或 `langfuse` 依赖项 | `langfuse` |
| 广泛涉及服务启动、生命周期或 `/health` | 最相关的一到两个目标 |
| 无特定变更，仅做定期发布前可信度检查 | `all` |

优先选择能够覆盖变更的**最小范围**目标。只有在有意执行完整检查时才运行包含重量级
LangFuse 技术栈的 `all`，不要习惯性地运行它。

## 运行方式

```sh
./scripts/smoke_test.sh                 # 默认：postgres、mongo、agui
./scripts/smoke_test.sh mongo           # 单个目标
./scripts/smoke_test.sh postgres agui   # 部分目标
./scripts/smoke_test.sh langfuse        # 重量级：约 5GB 技术栈，单独运行
./scripts/smoke_test.sh all             # 全部目标，包括 langfuse
```

默认运行不包含 `langfuse`，因为它会启动 LangFuse 完整的 6 服务自托管技术栈
（约 5GB 镜像）。成功运行时最终会输出 `--- All smoke tests passed ---`；任何目标失败
或传入未知目标时，退出码均为非零。

## 解读结果，以及为何“显示成功 ≠ 确实有效”的陷阱真实存在

该设计的核心在于：**API 测试通过并不能证明预期依赖项确实被使用。** 持久化测试只检查
两次调用之间的历史记录是否保留，任何正常工作的检查点存储器（包括 SQLite）都能满足
这一条件。因此，如果 `DATABASE_TYPE=mongo` 因环境变量丢失或回退逻辑而未能生效，
pytest 步骤仍会通过，但 Mongo 完全没有被访问。

正因如此，每个目标都会执行**第二项独立检查**，确认特定依赖项确实被使用。真正应当
信任的是以下输出行：

- `✓ verified: N postgres checkpoint rows for this run's thread`
- `✓ verified: N mongo checkpoint documents for this run's thread`
- `✓ verified: N LangFuse traces recorded for this run`
- `✓ verified: AG-UI streamed a complete run with the expected response`

每次运行都会使用**唯一的线程 ID**（`SMOKE_THREAD_ID`），因此即使面对非空数据卷，
计数也只反映*本次*运行的数据。出现 `✗ FAIL: … was NOT exercised as intended` 表示依赖项
实际上未被调用；应将其视为真实失败，而非偶发不稳定。

阅读失败信息时，还应了解以下保护措施：

- **端口已被占用：** 如果已有进程监听 `:8080`，`start_service` 会拒绝运行，避免健康
  检查意外地对无关进程返回成功。
- **服务启动时退出：** 脚本会立即失败并输出服务日志，而不是等待完整超时时间结束。
- **LangFuse 异步摄取：** 追踪数据经由 worker → ClickHouse 管道传递，因此脚本会短暂
  轮询追踪 API（通常只需几秒）后再判定结果。

## 云环境注意事项（网页版 Claude Code / 沙箱）

该脚本支持智能体在沙箱化云环境中运行。以下问题在这类环境中尤其常见：

- **Docker 守护进程可能未运行**，并且可能在不同步骤之间停止。如果 `docker` 报错
  “Cannot connect to the daemon”，请执行 `(dockerd > /tmp/dockerd.log 2>&1 &)` 启动
  守护进程，然后等待几秒。
- **不要在沙箱内构建服务镜像。** 容器构建无法通过出口代理访问包注册表（TLS/CA），
  因此 `docker compose build` 会失败。这正是脚本通过 `uv` 在**宿主机**上运行服务，
  而只将*依赖项*放入 Docker 的原因。不要通过将代理 CA 写入已提交的 Dockerfile 来
  “修复”此问题，因为它是沙箱特有的。
- **LangFuse 的注册表允许列表。** LangFuse 技术栈的 `minio` 镜像来自 `cgr.dev`
  （Chainguard）。在出口受限的环境中，应将 **`cgr.dev`** 加入网络允许列表，否则拉取
  请求会返回 403。（如果之后另一个主机的拉取仍返回 403，请添加错误信息中指出的主机；
  某些注册表会从独立 CDN 提供 blob。）
- **`pkill -f run_service.py` 会终止当前 shell。** `-f` 匹配完整命令行，而命令文本本身
  包含该字符串，所以会匹配到自身。需要释放 8080 端口时，请改用
  `fuser -k 8080/tcp`。
- **长时间在前台运行的 `sleep` 循环可能被沙箱终止。** 运行脚本或等待脚本时，应在后台
  启动（`nohup … &`）并轮询日志文件，不要在前台阻塞。
- **`docker compose exec` 需要 `.env`，而 `docker exec` 不需要。** 基于宿主机的流程
  有意不使用 `.env`；`docker compose exec` 会重新解析包含 `env_file: .env` 引用的完整
  配置，因此在缺少该文件时会失败。脚本通过 `docker exec <id>` 查询容器（ID 来自
  `docker compose ps -q`），从而规避此问题。

## 扩展脚本

该脚本遵循一个原则：**每个目标都必须证明真实依赖项已被使用，而不只是 API 返回了
某些内容。** 添加目标时必须保持这一原则。

添加新的可选依赖项目标时：

1. 按照现有结构添加 `smoke_<name>()` 函数：启动依赖项（Docker），使用正确的环境变量
   通过 `start_service` 在宿主机上启动服务，执行 API 层检查，然后执行**依赖项身份检查**
   （通过 `assert_positive_count` 直接查询依赖项中本次运行的数据，或像 AG-UI 目标一样
   对捕获的输出进行断言）。
2. 将目标注册到目标循环的 `case` 中；如果它应纳入完整检查，还要添加到 `all` 展开项。
   判断它的开销是否足够低，可以加入默认集合（类似数据库目标），还是足够高，应当设为
   按需运行（类似 LangFuse 目标）。
3. 如果目标需要附加 compose 文件，请遵循 `docker/compose.<dep>.yaml` 模式；如果它是
   大型外部技术栈，优先获取上游固定到特定标签的 compose 文件（如 `langfuse` 目标通过
   `LANGFUSE_REF` 所做的那样），不要将其复制进仓库。

不要将这些测试放入 CI docker 作业：它们位于 `tests/smoke/`（而非 CI 的
`test-docker` 作业所限定的 `tests/integration/`）并标记为 `@pytest.mark.docker`，
因此除非传入 `--run-docker`，否则会被跳过。
