---
name: dependency-refresh
description: >-
  为本仓库执行依赖项与版本刷新：更新 pyproject.toml/uv.lock 中的 Python 库、Docker
  基础镜像、GitHub Actions 固定版本、uv CLI 版本（CI + README + Dockerfile）、支持的
  Python 版本范围，以及 compose 和冒烟测试使用的基础设施镜像
  （postgres/mongo/LangFuse）。当用户要求“更新依赖项”“提升版本”或“执行依赖项刷新”，
  或每月定时执行 dependency-refresh 时使用。此操作手册自成一体；历史状态（暂缓的
  主版本、冷却日期）记录在上一次刷新 PR 中，步骤 0 会说明如何找到它。
---

# 依赖项与版本刷新

本技能是一份完整的操作手册。每轮更新了哪些内容、暂缓了哪些主版本等运行状态，
记录在**刷新 PR 的描述**中，而不是文件中。

本技能目录中的辅助参考资料应在相应步骤需要时阅读，而不是预先全部阅读：

- `references/coupling-constraints.md`：项目特有的耦合约束，以及历次刷新总结出的
  注意事项（langchain 与 langgraph 同步升级、检查点存储器主版本、Actions 运行时弃用、
  uv 的特殊行为等）。在分类处理前浏览各级标题；遇到解析器冲突或意外 CI 问题时
  阅读全文。
- `references/live-e2e.md`：真实端到端验证步骤（伪模型 HTTP 分层验证 + Streamlit
  浏览器测试）。

## 约定：以 PR 作为状态记录

每次刷新只生成一个 PR，它既交付变更，也记录下一次刷新所需的初始状态：

- **分支：** `claude/dependency-refresh-YYYY-MM-DD`
- **标题：** `chore(deps): dependency refresh YYYY-MM-DD`
- **正文部分**（模板）：

```markdown
## 更新内容
<固定版本提升、仅锁文件变更、Actions/uv CLI、基础设施镜像、基础镜像；
分类列出，并为所有 0.x 或主版本变更附上一行发行说明检查结论>

## 暂缓的主版本与冷却期
<沿用上一次刷新 PR 中的这张表：删除已经落地的行（并在“更新内容”中注明），
保留仍暂缓的行，添加新发现的主版本。每个关键依赖项的新主版本首次出现时都要添加一行。>

| 升级项 | 从 → 到 | 主版本发布日期 | 最早可升级日期 | 备注 / 投入产出比 |
| --- | --- | --- | --- | --- |

## 验证
<已运行的检查及其结果，包括重新运行了哪些冒烟测试目标，以及有意跳过了哪些目标和原因>
```

切勿在其他位置重复维护状态：不要创建日志文件或待办文档。如果某项经验属于*可复用知识*
（耦合约束或注意事项），应在同一 PR 中将其记录到
`references/coupling-constraints.md`。该文件记录知识，而非状态。

## 步骤 0：从上一次刷新 PR 恢复状态

修改任何固定版本前，先找到最近一次刷新 PR 并阅读其正文：

1. 在本仓库范围内搜索所有状态的 PR，按时间从新到旧排列，查询条件为
   `"dependency refresh" in:title`。
2. 备用方案：按源分支前缀 `head:claude/dependency-refresh` 搜索。
3. 最后手段：在 `main` 上通过 `git log` 查找修改 `uv.lock` 的提交，再查询对应 PR。

从最新的 PR（如果最新一次范围很窄，则查看最近两个）中提取：暂缓主版本表、所有
“有意推迟”的项目，以及验证注意事项（例如“MongoDB 未测试”）。用这张表作为本轮
分类处理的起点，并按上述模板继续维护。

## 冷却策略

分为两层，并尽可能使用同一种机制：

**1. 全局 14 天解析器冷却期（由 uv 强制执行）。** `pyproject.toml` 包含：

```toml
[tool.uv]
exclude-newer = "14 days"
```

每次解析（`uv lock`、`uv lock --upgrade`、`uv sync` 触发的重新锁定）只考虑发布至少
14 天的版本，因此被入侵或存在缺陷的新版本不会在风险最高的窗口期进入锁文件。这也会
自动限制固定版本提升：将 `~=` 固定版本提升到发布不足 14 天的版本，会导致解析失败并
显示 `exclude-newer` 提示。这表明策略正在生效，并非需要绕过的错误。应将该升级留到
下一轮，而不是覆盖此限制。

**安全例外（唯一获准的覆盖情形）：** 如果某个版本修复了影响本仓库的漏洞
（Dependabot PR、安全公告），无论其发布时间多短都应立即采用。为该包添加临时覆盖项
并附上说明；待该版本发布超过全局冷却期后，在下一次刷新中删除：

```toml
[tool.uv.exclude-newer-package]
somepkg = "0 days"  # 安全修复 CVE-XXXX；发布超过 14 天后删除
```

需要了解的冷却机制：时间跨度记录在 `uv.lock` 中（`exclude-newer-span`），重新锁定
具有稳定性，并且必须使用仓库固定的 uv 版本。参见
`references/coupling-constraints.md` 中与 uv 相关的条目。

**2. 关键依赖项的三个月主版本冷却期（通过流程强制执行）。** 关键依赖项是指主版本
升级一旦出问题，处理成本高昂或难以回退的依赖项：

- **基础设施镜像：** `postgres`、`mongo`（compose/冒烟测试）。主版本可能更改磁盘
  数据格式。
- **应用平台：** `streamlit`、`fastapi`、`pydantic`。
- **智能体核心技术栈：** `langgraph` + `langchain`，以及与之耦合的
  `langgraph-checkpoint-*` 包。

关键依赖项的新主版本必须在其 X.0.0 发布日期后等待**至少 3 个月**，且只能在专门的
独立 PR 中采用，绝不能混入安全升级轮次。首次发现该主版本的轮次，应在暂缓表中添加
一行，记录主版本发布日期和算出的“最早可升级日期”；后续轮次继续保留该行，直至升级
落地或被否决。满足日期不等于自动升级：到达该日期后，仍须按下文的主版本分类处理流程
评估，才能落地。非关键依赖项的主版本无须等待 3 个月，只需执行常规分类处理。

## 版本所在位置

| 内容 | 文件 |
| --- | --- |
| 运行时依赖项与固定版本 | `pyproject.toml` → `[project] dependencies` |
| 开发工具（ruff、pyrefly、pytest 等） | `pyproject.toml` → `[dependency-groups] dev` |
| 最小客户端/Streamlit 依赖项（主列表的子集，**需保持同步**） | `pyproject.toml` → `[dependency-groups] client` |
| 完整解析后的版本（实际安装内容的唯一事实来源） | `uv.lock` |
| 解析器冷却期 | `pyproject.toml` → `[tool.uv] exclude-newer` |
| lint/格式化所用的目标 Python | `pyproject.toml` → `[tool.ruff] target-version` |
| CI 测试矩阵 | `.github/workflows/test.yml`（`python-version`） |
| 容器基础镜像 | `docker/Dockerfile.app`、`docker/Dockerfile.service` |
| 支持的 Python 范围与分类器 | `pyproject.toml` → `requires-python`、`classifiers` |
| GitHub Actions 版本（`actions/checkout`、`setup-python`、`setup-uv`、`docker/*`、`codecov-action` 等） | `.github/workflows/*.yml`（`uses:`） |
| `uv` CLI 版本：**共四处，需保持同步** | `.github/workflows/test.yml`（两个 `setup-uv` 步骤的 `version:`）、`docker/Dockerfile.app` + `docker/Dockerfile.service`（`pip install uv==`）、`README.md` 快速入门（`curl .../uv/<version>/install.sh`） |
| compose 与冒烟测试使用的基础设施镜像 | `compose.yaml`（`postgres:` 标签）、`docker/compose.mongo.yaml`（`mongo:` 标签）、`scripts/smoke_test.sh`（`LANGFUSE_REF`） |

## 工作流程

1. **恢复状态。** 执行上述步骤 0。
2. **盘点。** 将 `uv.lock` 中解析出的版本与 PyPI 最新版本进行比较
   （`https://pypi.org/pypi/<package>/json` → `info.version`、
   `info.requires_dist`、`info.requires_python`；每个版本文件的
   `upload_time_iso_8601` 可用于检查冷却期）。还要盘点上表中的非 Python 部分：
   Actions 固定版本、uv CLI、Docker 基础镜像、基础设施镜像标签和 `LANGFUSE_REF`。
3. **分类处理**每个候选项（原则见下文），遵守两层冷却期。关键依赖项的新主版本应添加
   到暂缓表，而不是直接升级。
4. **应用安全升级：** 编辑 `pyproject.toml` 中的固定版本。保持现有风格：应用依赖项
   使用 `~=`，宽松固定的库使用 `>=` 下限。
5. **重新解析：** 运行 `uv lock --upgrade`。仔细阅读冲突信息，其中会指出阻止升级的
   确切传递约束（或冷却期）；查看 `references/coupling-constraints.md` 中是否已有相关
   说明。
6. **使固定版本与锁文件一致。** 如果解析器选择的版本高于所写的固定版本（常见于
   `>=` 下限），则提升所写的固定版本以匹配。此后运行 `uv lock` 必须不产生任何变更。
   **写入每个对齐后的下限前，须根据实际依赖方的要求进行合理性检查**：在 `uv.lock`
   中查找该包区块内反向依赖项的 `specifier`。否则，一个没有实际依赖方需要的下限可能
   会阻碍未来升级。
7. **同步并执行静态验证：** 依次运行 `uv sync --frozen`、`uv run ruff check .`、
   `uv run ruff format --check .`、`uv run pyrefly check`、`uv run pytest`。
8. **真实端到端测试：** 按照 `references/live-e2e.md` 执行（伪模型 HTTP 分层验证；
   UI 技术栈变更时执行 Streamlit 浏览器冒烟测试）。如果升级涉及检查点存储器、AG-UI
   或 LangFuse，还要运行相应的 `scripts/smoke_test.sh` 目标（参见冒烟测试技能；需要
   Docker 守护进程，如果未运行，可执行 `sudo dockerd &`）。
9. **PR：** 按上述约定创建分支、标题和正文。绝不推送到 `main`。在同一 PR 中将新的
   可复用注意事项记录到耦合约束参考文档。

## 分类处理原则

- 同一主版本内的**次版本/补丁版本升级**通常是安全的，可以批量处理。
- **主版本升级**（以及不承诺遵循 SemVer 稳定性的 1.0 之前 `0.x` 次版本升级）：结合
  仓库*实际使用*该包的方式，评估真实代码/行为变更和投入产出比；非简单升级应留到
  独立 PR。先搜索导入位置，仓库通常只使用 API 中很小且稳定的部分。
- **关键依赖项主版本**还必须等待三个月冷却期结束。
- **版本耦合的包**应同步升级，一个包升级会带动其他包。已知耦合关系记录在
  `references/coupling-constraints.md` 中，其余关系由解析器发现。
- **仅传递依赖项**（仓库中没有代码导入它们）对本仓库代码的风险较低；风险主要存在于
  使用它们的依赖项中（例如 Streamlit 与 pandas/pyarrow）。不要仅为了固定版本而将它们
  添加到 `[project] dependencies`。

## Python 版本策略

遵循 CPython 的发布周期：每年 10 月发布一个新次版本，支持约 5 年（约 18 个月的
缺陷修复期，之后仅提供安全修复）。保持声明的范围（`requires-python`）、分类器、CI
矩阵、ruff `target-version` 和 Docker 基础镜像相互一致，所有位置均列于上表。依赖项
技术栈能在新次版本上成功解析并通过测试后，即可采用该版本；旧次版本接近仅安全支持
结束日期，或必要依赖项率先停止支持时，应放弃该版本（numpy 通常是首要指标）。使用该
版本的真实解释器验证支持情况（运行 `uv python install 3.X`，然后执行完整检查流程），
并使用稳定补丁版本而不是 `rc` 版本（参见耦合约束参考文档）。
