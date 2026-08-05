# AGENTS.md

## 适用范围

本文件适用于整个仓库。Codex 从仓库根目录启动时应先读取本文件；若子目录以后增加更具体的 `AGENTS.md` 或 `AGENTS.override.md`，以离目标文件最近的指令为准。

## 开发环境与命令

- 本项目使用 Python 3.12 至 3.14，并使用 `uv` 管理环境和依赖。
- 首次设置或依赖发生变化时运行 `uv sync`。
- 修改 Python 代码后，按范围运行 `uv run pytest`。
- 提交前运行 `uv run ruff check .`、`uv run ruff format --check .` 和 `uv run pyrefly check`。
- 只运行与改动相关的重量级集成测试；需要 Postgres、MongoDB、AG-UI 或 LangFuse 时使用 `$smoke-test` 判断目标。

## 工作约定

- 先阅读相关实现和测试，再修改代码；优先沿用现有结构、命名与依赖。
- 保持改动聚焦，不做与当前任务无关的重构，不覆盖用户已有的工作区改动。
- 不要自行创建分支、提交、推送、发布、关闭 issue 或创建 PR，除非用户明确要求相应操作。
- 修改行为时添加或更新覆盖该行为的测试。若无法运行某项检查，在最终结果中说明原因。

## 注释风格

- 默认不要在新增或编辑的代码中添加注释。
- 仅当代码背后的原因并不明显，例如存在隐含约束、微妙的不变量或需要规避特定错误时，才添加简短注释。
- 不要用注释解释代码做了什么；清晰的命名应当足以表达行为。
- 全新文件可以在开头添加简短且确有帮助的模块级文档字符串。
- 编辑现有文件时保持其原有注释密度和风格。

## Codex 技能

- 仓库级技能位于 `.agents/skills/<skill-name>/SKILL.md`，这是 Codex 的规范发现路径。
- 可用技能为 `$dependency-refresh`、`$maintainer-response`、`$model-refresh` 和 `$smoke-test`。用户点名技能或任务与技能描述匹配时，先完整读取对应 `SKILL.md`，再按其指引执行。
- 技能引用的详细资料保存在同一技能目录的 `references/` 中，只在 `SKILL.md` 指定的步骤读取。

## Codex 钩子

- 项目钩子在 `.codex/config.toml` 中注册，脚本放在 `.codex/hooks/`。Codex 仅在仓库受信任且用户通过 `/hooks` 信任当前钩子定义后运行项目钩子。
- `ensure-pinned-uv.sh` 只在 `CODEX_ENSURE_PINNED_UV=true` 时安装 Dockerfile 中固定的 `uv` 版本。只应在隔离的 Codex cloud 环境中启用；本地开发默认保持关闭。
- Codex cloud 的首选做法是在环境设置中固定运行时和包版本，并在 setup script 中安装依赖；不要依赖 agent 阶段临时联网。

## 维护者脚手架与模板内容

本仓库是 GitHub 模板。下游仓库首次从模板创建时，`.github/workflows/template-cleanup.yml` 会移除仅供维护者使用的脚手架。

- 维护者自动化内容放在 `.agents/`、`.codex/` 或 `docs/maintenance/` 中；清理流程会整体删除这些目录。
- 仅供维护者使用的工作流必须包含所有者守卫条件 `if: github.repository == 'JoshuaC215/agent-service-toolkit'`，清理流程会按该条件识别并移除工作流。
- 只有当维护者文件必须放在共享目录且无法携带守卫标记时，才将其加入 `template-cleanup.yml` 的显式列表。
