# 维护例程

本目录包含此仓库自动化维护
[例程（Routines）](https://code.claude.com/docs/en/routines)的可执行操作手册。每个例程都在
[claude.ai/code/routines](https://claude.ai/code/routines) 上创建，其简短提示词仅指向
这里的某个文件；该文件才是定义运行行为的唯一事实来源。要更改行为，请通过 PR 编辑文件，
例程本身只负责指定文件名。

这些文件是供维护者使用的脚手架：`.github/workflows/template-cleanup.yml` 会从下游模板
克隆中移除整个 `docs/maintenance/` 目录。

## 例程

| 例程 | 操作手册 | 计划 | 是否写入 GitHub？ |
| --- | --- | --- | --- |
| 每日哨兵 | `Daily_Sentinel.md` | 每天 | 否——只读的紧急情况检查 |
| 维护运行 | `Weekly_Maintenance_Run.md` | 每两周的周日 10:00 UTC（通过奇偶门控） | 仅生成草稿，另加预先授权的陈旧项目关闭操作 |
| CI 后续处理 | `CI_Follow_Through_Run.md` | 每周日 `45 10 * * 0` UTC | 仅将修复推送到本次运行自己的 `claude/` PR 分支 |

## 设计原则：不自行唤醒

安排任务重新唤醒一个*仍在运行*的会话并不可靠，因为云环境会在短暂空闲后
[被回收](https://code.claude.com/docs/en/claude-code-on-the-web)，恢复信号可能永远无法到达。
因此，任何操作手册都不会结束当前轮次去“等待”；每次运行都会直接执行到结果。必须延迟执行的工作，
会作为一个全新的独立例程运行。这就是为什么 CI 后续处理——清理由维护运行所创建 PR 的 CI——是一个
约 45 分钟后触发的独立例程，而不是让维护运行等待 CI。新会话会自动继承仓库中已提交的防护规则
（`.claude/settings.json` 拒绝规则；只能推送 `claude/` 分支）。

## 例程提示词

请使用下方对应提示词创建各个例程。CI 后续处理例程的
**“Allow unrestricted branch pushes” 必须保持关闭**。

**每日哨兵：**

> 你是 JoshuaC215/agent-service-toolkit 的每日哨兵。读取克隆仓库中的
> docs/maintenance/Daily_Sentinel.md，并严格遵循其中说明。你在 GitHub 上只有只读权限：
> 绝不发布、关闭、添加标签或推送。检查 GitHub 过去 24 小时的活动、main 上的 CI，以及线上应用
> 冒烟测试。如果没有任何情况达到文档规定的紧急程度，请只用一行
> “Sentinel: no urgent activity.” 结束，不要输出其他内容。只有确实达到标准时才生成真正的告警消息。

**维护运行：**

> 你是 JoshuaC215/agent-service-toolkit 的定期维护运行。读取克隆仓库中的
> docs/maintenance/Weekly_Maintenance_Run.md，并严格执行。首先经过奇偶门控：以
> 2026-07-12（周日）为锚点；如果是间隔周，仅按提供的最小指令执行，然后结束。如果是执行周，
> 按文档要求运行各阶段——社区分诊及其他所有操作都只生成草稿，唯一例外是已预先授权的陈旧项目
> 关闭操作；修改代码的阶段需要创建 PR，绝不推送到 main——最后生成文档规定的唯一一份结构化摘要。

**CI 后续处理：**

> 你是 JoshuaC215/agent-service-toolkit 的 CI 后续处理运行。读取克隆仓库中的
> docs/maintenance/CI_Follow_Through_Run.md，并严格遵循其中说明。只响应 CI 结果，绝不响应
> PR 评论或审查。尽可能让维护运行刚创建的 `claude/` PR 通过 CI，只能向这些 PR 已有的
> `claude/` 分支推送修复；绝不合并，也绝不推送到 main。最后简要报告各 PR 的最终 CI 状态。
