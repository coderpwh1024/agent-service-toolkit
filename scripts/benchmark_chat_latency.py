"""Compare chat prefixes, cache modes and models without executing selected tools.

Run from the repository root with PYTHONPATH=src uv run python scripts/benchmark_chat_latency.py.
"""

import argparse
import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median
from time import monotonic
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import LanguageModelInput
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI
from pydantic import Field

from agents.mem0_chatbot import _chat_prompt
from agents.research_delegation import _RULES, answer_with_research, research_assistant
from core import settings
from core.logging import configure_logging
from service.nacos import NacosIntegration
from skills.video_chat import VideoSkillSession

QUESTIONS = (
    "你好，请用一句话简单介绍一下你自己。",
    "请用两句话解释为什么天空看起来是蓝色的。",
    "我今天有点累，请用两句话陪我聊聊，不要给建议。",
)


class PromptCapture(FakeMessagesListChatModel):
    messages_seen: list[BaseMessage] = Field(default_factory=list)
    tools_seen: list[Any] = Field(default_factory=list)

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[LanguageModelInput, AIMessage]:
        self.tools_seen = list(tools)
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.messages_seen = list(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


@dataclass(frozen=True)
class Trial:
    name: str
    model: str
    system: str
    explicit_cache: bool = False
    legacy_layout: bool = False


async def build_prompt(trial: Trial, question: str) -> tuple[list[BaseMessage], list[dict]]:
    human = HumanMessage(content=question)
    config = RunnableConfig(
        configurable={
            "user_id": "7",
            "authenticated_user_id": "7",
            "thread_id": "latency-probe",
            "model": trial.model,
        }
    )
    session = VideoSkillSession(human, None, [])
    if trial.legacy_layout:
        messages = [
            SystemMessage(content=trial.system),
            SystemMessage(
                content=settings.mem0_prompts.memory_status_template.format(status="available")
            ),
            SystemMessage(
                content=_RULES
                + session.rules(config)
                + f"\n当前日期：{datetime.now(UTC).date().isoformat()}。"
            ),
            human,
        ]
        return messages, [
            convert_to_openai_tool(research_assistant),
            convert_to_openai_tool(session.load_tool()),
        ]
    capture = PromptCapture(responses=[AIMessage(content="offline capture")])
    prompt = _chat_prompt([human], [], "available")
    prompt[0] = SystemMessage(content=trial.system)
    previous = settings.DASHSCOPE_EXPLICIT_CACHE_ENABLED
    try:
        settings.DASHSCOPE_EXPLICIT_CACHE_ENABLED = trial.explicit_cache
        await answer_with_research(capture, prompt, config, video=session)
    finally:
        settings.DASHSCOPE_EXPLICIT_CACHE_ENABLED = previous
    return capture.messages_seen, [convert_to_openai_tool(tool) for tool in capture.tools_seen]


async def run_trials(args: argparse.Namespace) -> dict[str, Any]:
    integration = NacosIntegration(settings)
    clients = {}
    rows = []
    try:
        await integration.load_remote_config()
        baseline = (
            Path(args.baseline_prompt).read_text()
            if args.baseline_prompt
            else settings.mem0_prompts.chat_system
        )
        candidate = Path(args.candidate_prompt).read_text() if args.candidate_prompt else baseline
        trials = [
            Trial("baseline_max", "qwen3.8-max", baseline, legacy_layout=True),
            Trial("optimized_max", "qwen3.8-max", candidate),
            Trial("explicit_max", "qwen3.8-max", candidate, explicit_cache=True),
            Trial("optimized_flash", "qwen3.8-flash", candidate),
        ]
        for sample in range(args.samples):
            order = trials[sample % len(trials) :] + trials[: sample % len(trials)]
            for trial in order:
                messages, tools = await build_prompt(trial, QUESTIONS[sample % len(QUESTIONS)])
                if trial.model not in clients:
                    clients[trial.model] = ChatOpenAI(
                        model=trial.model,
                        base_url=settings.DASHSCOPE_BASE_URL,
                        api_key=settings.DASHSCOPE_API_KEY,
                        streaming=True,
                        stream_usage=True,
                        extra_body={"enable_thinking": False},
                    )
                model = clients[trial.model]
                payload = model._get_request_payload(
                    messages,
                    tools=tools,
                    stream=True,
                    stream_options={"include_usage": True},
                    max_tokens=args.max_tokens,
                )
                started = monotonic()
                first = None
                usage = {}
                finish = None
                try:
                    async with asyncio.timeout(30):
                        stream = await model.async_client.create(**payload)
                        async with stream:
                            async for chunk in stream:
                                if chunk.usage:
                                    usage = chunk.usage.model_dump()
                                for choice in chunk.choices:
                                    if (
                                        choice.delta.content
                                        and choice.delta.content.strip()
                                        and first is None
                                    ):
                                        first = monotonic() - started
                                    if choice.finish_reason:
                                        finish = choice.finish_reason
                    details = usage.get("prompt_tokens_details") or {}
                    row = {
                        "trial": trial.name,
                        "sample": sample + 1,
                        "model": trial.model,
                        "first_text_ms": round(first * 1000) if first is not None else None,
                        "total_ms": round((monotonic() - started) * 1000),
                        "input_tokens": usage.get("prompt_tokens"),
                        "cached_tokens": details.get("cached_tokens"),
                        "cache_creation_tokens": details.get("cache_creation_input_tokens"),
                        "output_tokens": usage.get("completion_tokens"),
                        "finish_reason": finish,
                    }
                except Exception as exc:
                    body = getattr(exc, "body", {})
                    error = body.get("error", body) if isinstance(body, dict) else {}
                    code = error.get("code") if isinstance(error, dict) else None
                    row = {
                        "trial": trial.name,
                        "sample": sample + 1,
                        "error_type": type(exc).__name__,
                        "error_code": code,
                    }
                    if code == "Arrearage":
                        rows.append(row)
                        raise RuntimeError(
                            "Model account is unavailable; benchmark stopped"
                        ) from None
                rows.append(row)
                print(json.dumps(row, ensure_ascii=False), flush=True)
        summary = {}
        for trial in trials:
            valid = [
                row
                for row in rows
                if row["trial"] == trial.name
                and row.get("first_text_ms") is not None
                and row.get("finish_reason") == "stop"
            ]
            times = [row["first_text_ms"] for row in valid]
            summary[trial.name] = {
                "valid_samples": len(times),
                "mean_ms": round(mean(times)) if times else None,
                "median_ms": median(times) if times else None,
                "min_ms": min(times) if times else None,
                "max_ms": max(times) if times else None,
            }
        report = {
            "samples_per_trial": args.samples,
            "baseline_prompt_characters": len(baseline),
            "candidate_prompt_characters": len(candidate),
            "candidate_digest": hashlib.sha256(candidate.encode()).hexdigest(),
            "baseline_digest": hashlib.sha256(baseline.encode()).hexdigest(),
            "rows": rows,
            "summary": summary,
        }
        if args.output:
            Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"summary": summary}, ensure_ascii=False), flush=True)
        return report
    finally:
        for model in clients.values():
            await model.root_async_client.close()
            await asyncio.to_thread(model.root_client.close)
        await integration.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-prompt", help="Explicit evaluation-only prompt file; never a service fallback"
    )
    parser.add_argument(
        "--baseline-prompt",
        help="Optional historical baseline template for reproducible comparisons",
    )
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=256)
    parser.add_argument("--output")
    args = parser.parse_args()
    if not 1 <= args.samples <= 20 or not 1 <= args.max_tokens <= 1024:
        parser.error("samples must be 1–20 and max-tokens 1–1024")
    configure_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
    logging.getLogger().setLevel(logging.WARNING)
    asyncio.run(run_trials(args))


if __name__ == "__main__":
    main()
