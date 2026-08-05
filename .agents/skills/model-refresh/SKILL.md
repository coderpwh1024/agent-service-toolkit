---
name: model-refresh
description: >-
  定期根据各提供商当前发布的模型审查 src/schema/models.py 中的 LLM 模型目录：
  添加新发布的模型，移除或标记提供商已弃用或不再推荐的模型，并将 DEFAULT_MODEL
  回退值重新指向当前可用的模型。当用户要求“检查新模型”“更新模型列表”
  “刷新模型目录”，或定时 model-refresh 触发器运行时使用。
---

# 刷新模型目录

保持 `src/schema/models.py`（`AllModelEnum` 目录）为最新状态：添加提供商自上次
刷新后发布的模型，移除提供商已弃用或正积极引导用户停止使用的模型，并确保
`src/core/settings.py` 中各提供商的 `DEFAULT_MODEL` 回退值指向仍然存在的模型。

## 模型配置位置

| 内容 | 文件 |
|---|---|
| 按提供商划分的所有受支持模型枚举，每个类的文档字符串中包含提供商文档 URL | `src/schema/models.py`（`AllModelEnum`） |
| 各提供商的默认模型，以及设置密钥后哪些模型为“可用”状态 | `src/core/settings.py`（`Settings.model_post_init`） |
| 枚举到 API 模型字符串的映射，以及提供商特有的构造差异（温度、流式传输、工具绑定） | `src/core/llm.py`（`_MODEL_TABLE`、`get_model`） |
| 断言 `get_model` 为每个模型构造正确 LangChain 类的单元测试 | `tests/core/test_llm.py` |
| 设置和默认模型测试 | `tests/core/test_settings.py` |
| 实时冒烟测试（真实 API 调用） | `scripts/check_live_models.py` |

## 工作流程

1. **调研。** 对于 `src/schema/models.py` 中的每个提供商代码块，获取该类文档
   字符串中已有的文档 URL，并将当前模型列表与已有的枚举值进行比较。这些文档字符串
   URL 是查找信息的**唯一权威来源**，不要在其他位置硬编码另一份副本，因为提供商的
   文档 URL 会随时间变化，重复的链接会在无人察觉的情况下失效。
   - **如果文档字符串中的 URL 返回 404、重定向到通用落地页，或因其他原因不再指向
     模型列表：**查找该提供商当前的规范模型文档 URL，在同一次变更中更新文档字符串，
     然后从修正后的页面继续调研。不要仅仅因为旧链接失效就跳过该提供商。
   - 少数提供商除文档字符串 URL 外还需要额外检查：Azure OpenAI 的发布进度落后于
     OpenAI 自身（它基于部署，因此要检查 Azure 当前支持哪些基础模型）；Anthropic
     有单独的弃用页面，值得检查模型的停用日期；Vertex AI 模型路径有时与 Gemini API
     中同一模型的名称不同（例如 `models/gemini-2.5-flash` 与
     `gemini-2.5-flash`），因此要检查两列。Ollama 在运行时使用用户提供的模型名称，
     无需为它添加任何模型；只需确认 `llm.py` 中的通用透传仍然有效。
   - **`docs.aws.amazon.com` 可能会向直接网页抓取返回 HTTP 403**（它会拦截抓取工具的
     user agent，这并非出口策略拒绝）。改用 Codex 的网页搜索查找 Bedrock 模型 ID；搜索
     结果可以显示模型卡片和推理配置文件支持页面。每个模型的
     卡片页面（`.../model-card-anthropic-claude-<model>.html`）会列出准确的
     `modelId` 和配置文件 ID。
2. **对每项差异进行分类：**
   - *新模型，已正式发布* → 添加。
   - *新模型，预览版或实验版* → 视情况判断；当没有正式发布的替代模型时，本仓库曾
     接受过预览模型（例如 `gemini-3-pro-preview`），但有正式版时应优先使用正式版。
   - *现有模型已被提供商弃用或停止服务* → 移除，除非它是该提供商仅剩的模型
     （此时应标记该情况，不要让目录为空）。
   - *现有模型只是已被取代，但仍在提供服务* → 保留，除非提供商文档明确要求迁移。
3. 添加成员时，**遵循现有命名约定**：
   - 枚举成员名称使用 `SCREAMING_SNAKE_CASE`，通常由系列和尺寸组成，例如
     `SONNET_45`、`GEMINI_25_PRO`、`LLAMA_33_70B`。版本号省略小数点
     （`4.5` → `45`）。
   - 枚举值必须是提供商 API 所要求的准确字符串（`claude-sonnet-4-5`、
     `gemini-2.5-pro`、`gpt-5.1`），应从提供商文档中原样复制，不要猜测。
   - 在类中将同一提供商的模型系列归组，并大致按尺寸和代际排序，与当前的排列方式保持一致。
4. **在所有耦合位置应用变更**，不要只编辑枚举：
   - `src/schema/models.py`：添加或移除 `StrEnum` 成员。保持文档字符串中的 URL
     为最新状态（参见步骤 1）。
   - `src/core/settings.py` 的 `model_post_init`：如果被移除的模型是
     `DEFAULT_MODEL` 回退值，将其重新指向该提供商剩余的模型，最好选择便宜、快速的
     模型。应符合当前默认值的定位（例如 Haiku、Flash、Nano 级别，而非旗舰模型）。
   - `src/core/llm.py`：对于单纯的重命名或添加，无需改动（`_MODEL_TABLE` 和
     `if model_name in ...Name` 分派由枚举驱动），但要检查是否存在也应适用于新模型的
     提供商特有逻辑（参见 Groq 的 safeguard 模型分支）。
   - `tests/core/test_llm.py`、`tests/core/test_settings.py`：更新所有硬编码了
     被移除模型值的测试；如果重要的新模型需要特殊处理（温度覆盖、工具绑定差异等），
     则为其添加测试用例。
   - `.env.example` 及其他文档：仅当其中明确提到已变更的特定模型时才更新
     （大多数情况下并未提及）。
5. **调研期间不要改动提供商凭据，也不要添加实时网络调用。** 这一步只涉及调研和
   代码编辑，不需要 API 密钥。
6. **对变更进行实时测试**（需要 API 密钥，参见下方“实时测试”）。
7. **总结**添加、移除和重新指向的内容及原因，并为每项变更引用提供商文档。明确标记
   所有模型重命名：对于在 `DEFAULT_MODEL` 或 `AVAILABLE_MODELS` 环境配置中固定了
   旧枚举值的现有部署，这属于破坏性变更，不能静默替换。按照仓库的常规 Git 工作流
   提交并推送；仅在用户要求时创建 PR。

## 无法实时验证的提供商（Bedrock、Azure、Vertex 服务账号、DeepSeek、OpenRouter）

没有某个提供商的密钥**不代表**可以跳过它，因为过时或无效的目录条目比基于文档的
条目更糟糕。像处理其他提供商一样根据文档更新这些条目，并在 PR 中将其明确标记为
**仅依据文档，未经验证**；引用提供商页面，并说明下列注意事项，让下一位持有密钥的
人员准确知道需要抽查什么。仅当文档本身含糊不清，**并且**该变更属于产品决策
（例如增加全新的定价层级）而非新鲜度更新时，才保留该提供商不变。

- **AWS Bedrock**：枚举*值*会直接传给 `ChatBedrock(model_id=...)`，因此它必须是
  真实的 Bedrock ID，而不是易读标签。需要注意两点：
  1. 最新的 Claude 模型**无法通过其基础模型 ID 按需调用**。直接使用
     `anthropic.claude-...` 调用会返回 400，并提示“on-demand throughput isn't
     supported”。必须通过跨区域**推理配置文件**调用：在基础 ID 前添加地理区域前缀
     （`us.`、`eu.`、`apac.`）或 `global.`。优先使用 `global.`，因为它能动态路由且
     不依赖区域，最适合没有区域上下文的目录值；同时在 PR 中注明，未加入 Global CRIS
     的单区域部署应将此前缀替换为其所在区域的前缀。
  2. Bedrock 继承了与直接使用 Anthropic API 相同的**采样参数限制**，例如 Sonnet 5
     系列模型会拒绝 `temperature`。如果将 Bedrock 条目指向此类模型，应在 `llm.py`
     的 Anthropic 和 Bedrock 分派中复用已有的不传 `temperature` 分支。
  如果环境中恰好存在 AWS 凭据，使用
  `boto3.client("bedrock").list_foundation_models()` 是确认真实 ID 的最快方式；但要
  注意，Bedrock 访问权限需要单独启用，即使其他 AWS 调用能正常工作，此调用仍可能因
  认证错误而失败。
- **Azure OpenAI** 是真正需要较多工作的提供商，因为它基于部署，且目录与更多位置耦合：
  - `settings.py` 的 `model_post_init` 硬编码了 `required_models` 集合（目前为
    `{"gpt-4o", "gpt-4o-mini"}`），并用它验证 `AZURE_OPENAI_DEPLOYMENT_MAP`。
    更新枚举也意味着要更新该集合、`.env.example` 中的部署映射示例，以及
    `tests/core/test_settings.py` 中约 7 个 Azure 测试用例。
  - `llm.py` 为 Azure 路径硬编码了 `temperature=0.5`，但 Azure 的 GPT-5 时代
    **推理**变体会拒绝 `temperature`（400）。如果将 Azure 迁移到此类模型，应添加
    一个类似 Anthropic/Bedrock Sonnet 5 处理方式的不传 `temperature` 分支。
  - 修改 Azure 枚举值会对每个用户的部署映射造成**破坏性变更**，因为用户会以这些键
    命名部署。应将 Azure 的代际升级作为单独变更进行评审，并明确强调部署映射会中断，
    不要将其静默混入例行刷新。

## 实时测试

`scripts/check_live_models.py` 会向环境中已配置凭据的每个提供商的每个模型发送一个
只要求返回一个词的简单提示，并按模型报告 PASS、FAIL 或 SKIP。它被特意放在 pytest
测试套件之外，因为它会发起真实网络调用并产生极少量真实费用。请在已填充密钥的情况下
手动运行，或通过定时触发器运行：

```sh
PYTHONPATH=src uv run python scripts/check_live_models.py                    # 所有已配置的提供商
PYTHONPATH=src uv run python scripts/check_live_models.py --provider anthropic google
```

`SKIP` 表示该提供商没有可用凭据，这是预期行为，不算失败。只有 `FAIL` 行才应阻塞构建。
成本很低（每个提供商只有少量、每次仅使用几个 token 的补全），但仍会真实消耗密钥对应
账户的费用，因此不要将其接入 CI，也不要在每次提交时运行。

如果当前环境中没有配置任何提供商凭据，请完全跳过此步骤，不要因此失败。调研和编辑步骤
本身仍然完全有用。
