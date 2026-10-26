import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai.chat_models.base import _convert_message_to_dict

from core import settings
from core.chat_context import prepare_chat_prompt


def test_dynamic_context_does_not_change_fixed_prefix_or_mutate_input():
    original = [
        SystemMessage(content="persona"),
        HumanMessage(content="previous"),
        AIMessage(content="answer"),
        SystemMessage(content="available", name="runtime_context"),
        HumanMessage(content="current"),
    ]
    first = prepare_chat_prompt(
        original, fixed_rules="tools", runtime_context="today", model_name="qwen3.8-max"
    )
    changed = list(original)
    changed[-2] = SystemMessage(content="search_failed", name="runtime_context")
    second = prepare_chat_prompt(
        changed, fixed_rules="tools", runtime_context="tomorrow", model_name="qwen3.8-max"
    )
    assert [m.content for m in first[:4]] == [m.content for m in second[:4]]
    assert original[-2].content == "available"
    assert first[-1].content == second[-1].content == "current"
    assert [m.content for m in first if isinstance(m, (HumanMessage, AIMessage))] == [
        "previous",
        "answer",
        "current",
    ]


@pytest.mark.parametrize(
    "model,enabled,expected",
    [
        ("qwen3.8-max", True, True),
        ("qwen3.8-flash", True, False),
        ("gpt-5-mini", True, False),
        ("fake", True, False),
        ("qwen3.8-max", False, False),
    ],
)
def test_cache_marker_only_on_supported_fixed_prefix(monkeypatch, model, enabled, expected):
    monkeypatch.setattr(settings, "DASHSCOPE_EXPLICIT_CACHE_ENABLED", enabled)
    prompt = prepare_chat_prompt(
        [SystemMessage(content="persona"), HumanMessage(content="current")],
        fixed_rules="fixed tools",
        runtime_context="dynamic",
        model_name=model,
    )
    body = [_convert_message_to_dict(m) for m in prompt]
    marked = [m for m in body if "cache_control" in str(m)]
    assert len(marked) == int(expected)
    if expected:
        assert marked[0]["content"][0]["text"] == "fixed tools"
    assert "cache_control" not in str(body[-2:])
    assert body[-1]["content"] == "current"


def test_no_static_prefix_is_invented_for_empty_rules(monkeypatch):
    monkeypatch.setattr(settings, "DASHSCOPE_EXPLICIT_CACHE_ENABLED", True)
    result = prepare_chat_prompt(
        [HumanMessage(content="hello")],
        fixed_rules="",
        runtime_context="date",
        model_name="qwen3.8-max",
    )
    assert result[-1].content == "hello"
    assert result[0].name == "runtime_context"
    assert "cache_control" not in str(result)
