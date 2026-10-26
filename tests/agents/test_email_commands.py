import importlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.email_commands import handle_email_command
from core import settings
from core.skills import EMAIL_SKILL_NAME, EmailSkill
from schema.email_skill import EmailContent, EmailIntent, EmailScheduleError, EmailSendResult
from skills import email


@pytest.fixture
def email_runtime(monkeypatch):
    service = Mock(send=AsyncMock(return_value=EmailSendResult(status="sent")))
    monkeypatch.setattr(email, "_service", service)
    monkeypatch.setattr(settings, "EMAIL_SKILL_ENABLED", True)
    monkeypatch.setattr(
        settings,
        "_email_skill",
        EmailSkill(EMAIL_SKILL_NAME, "发邮件", "Workflow reference", "digest"),
    )
    structured = Mock(
        ainvoke=AsyncMock(
            return_value=EmailIntent(action="send", subject="会议提醒", body="明天下午三点开会。")
        )
    )
    structured.with_config.return_value = structured
    model = Mock(
        with_structured_output=Mock(return_value=structured),
        ainvoke=AsyncMock(return_value=AIMessage(content="普通对话")),
    )
    return service, model, structured


def config(user="7"):
    return {
        "configurable": {
            "user_id": user,
            "authenticated_user_id": user,
            "thread_id": "thread-email",
        }
    }


@pytest.mark.asyncio
async def test_spoken_request_sends_only_to_authenticated_identity(email_runtime):
    service, model, structured = email_runtime
    response, draft = await handle_email_command(
        HumanMessage(
            content="给我发邮件，主题是会议提醒，正文是明天下午三点开会。", id="voice-input-turn"
        ),
        None,
        model,
        config(),
    )
    assert response.response_metadata["email_status"] == "sent"
    assert draft is None
    service.send.assert_awaited_once_with(
        "7", "voice-input-turn", EmailContent(subject="会议提醒", body="明天下午三点开会。")
    )
    assert structured.with_config.call_args.kwargs["tags"] == ["skip_stream"]
    assert "untrusted" in structured.ainvoke.await_args.args[0][0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [None, "8"])
async def test_missing_or_forged_authenticated_identity_cannot_send(email_runtime, user):
    service, model, _ = email_runtime
    invocation = config()
    invocation["configurable"]["authenticated_user_id"] = user
    response, _ = await handle_email_command(
        HumanMessage(content="给我发邮件"), None, model, invocation
    )
    assert response.response_metadata["email_status"] == "unauthenticated"
    model.with_structured_output.assert_not_called()
    service.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action,recipient,status",
    [
        ("none", "current_user", None),
        ("cancel", "current_user", "rejected"),
        ("send", "other", "rejected"),
    ],
)
async def test_non_send_intents_do_not_deliver(email_runtime, action, recipient, status):
    service, model, structured = email_runtime
    structured.ainvoke.return_value = EmailIntent(
        action=action, recipient=recipient, subject="主题", body="正文"
    )
    response, _ = await handle_email_command(
        HumanMessage(content="给我发邮件"), None, model, config()
    )
    assert (response.response_metadata["email_status"] if response else None) == status
    service.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_content_is_collected_before_send(email_runtime):
    service, model, structured = email_runtime
    structured.ainvoke.return_value = EmailIntent(action="draft", subject="提醒")
    response, draft = await handle_email_command(
        HumanMessage(content="给我发邮件，主题是提醒"), None, model, config()
    )
    assert response.response_metadata["email_status"] == "draft"
    service.send.assert_not_awaited()
    structured.ainvoke.return_value = EmailIntent(action="send", subject="提醒", body="下午开会")
    response, draft = await handle_email_command(
        HumanMessage(content="正文是下午开会", id="follow-up"), draft, model, config()
    )
    assert response.response_metadata["email_status"] == "sent"
    assert draft is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError("secret"), TimeoutError()])
async def test_intent_failure_is_controlled_and_does_not_send(email_runtime, failure):
    service, model, structured = email_runtime
    structured.ainvoke.side_effect = failure
    response, _ = await handle_email_command(
        HumanMessage(content="给我发邮件"), None, model, config()
    )
    assert response.response_metadata["email_status"] == "invalid"
    assert "secret" not in response.content
    service.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_headers_are_rejected_after_model_extraction(email_runtime):
    service, model, structured = email_runtime
    structured.ainvoke.return_value = EmailIntent(
        action="send", subject="主题\r\nBcc: bad@example.com", body="正文"
    )
    response, _ = await handle_email_command(
        HumanMessage(content="给我发邮件"), None, model, config()
    )
    assert response.response_metadata["email_status"] == "invalid"
    service.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_ordinary_mailbox_fact_does_not_trigger_skill(email_runtime):
    service, model, _ = email_runtime
    response, draft = await handle_email_command(
        HumanMessage(content="我的邮箱是什么？"), None, model, config()
    )
    assert response is None and draft is None
    model.with_structured_output.assert_not_called()
    service.send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_name", ["chatbot", "mem0_chatbot"])
async def test_both_streaming_agents_execute_skill_and_checkpoint_result(
    email_runtime, monkeypatch, agent_name
):
    service, model, _ = email_runtime
    module = importlib.import_module(f"agents.{agent_name}")
    monkeypatch.setattr(module, "get_model", lambda _, **kwargs: model)
    if agent_name == "mem0_chatbot":
        monkeypatch.setattr(module, "get_mem0_service", lambda: None)
    graph = getattr(module, agent_name).copy()
    graph.checkpointer = MemorySaver()
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="给我发邮件", id="voice-input-email")]}, config()
    )
    assert result["messages"][-1].response_metadata["email_status"] == "sent"
    assert service.send.await_count == 1
    model.ainvoke.assert_not_awaited()
    checkpoint = await graph.checkpointer.aget_tuple(config())
    assert checkpoint.checkpoint["channel_values"]["__previous__"]["email_dialogue"] is None


@pytest.mark.asyncio
async def test_spoken_relative_time_has_runtime_clock_and_schedules(email_runtime):
    service, model, structured = email_runtime
    target = datetime.now(UTC) + timedelta(minutes=10)
    structured.ainvoke.return_value = EmailIntent(
        action="send", subject="提醒", body="开会", timing="scheduled", send_at=target
    )
    service.send.return_value = EmailSendResult(status="scheduled", send_at=target)
    response, draft = await handle_email_command(
        HumanMessage(content="十分钟后给我发邮件，主题是提醒，正文是开会", id="voice-schedule"),
        None,
        model,
        config(),
    )
    assert response.response_metadata["email_status"] == "scheduled"
    assert "已预约" in response.content and "尚未发送" in response.content
    service.send.assert_awaited_once_with(
        "7", "voice-schedule", EmailContent(subject="提醒", body="开会"), send_at=target
    )
    data = json.loads(structured.ainvoke.await_args.args[0][1].content)
    reference = datetime.fromisoformat(data["current_time"])
    assert reference.utcoffset() == timedelta(hours=8)
    assert data["timezone"] == "Asia/Shanghai"
    assert draft is None


@pytest.mark.asyncio
@pytest.mark.parametrize("timing", ["scheduled", "unclear"])
async def test_missing_delivery_time_never_falls_back_to_immediate(email_runtime, timing):
    service, model, structured = email_runtime
    structured.ainvoke.return_value = EmailIntent(
        action="send", subject="提醒", body="开会", timing=timing
    )
    response, draft = await handle_email_command(
        HumanMessage(content="晚点给我发邮件，主题是提醒，正文是开会"), None, model, config()
    )
    assert response.response_metadata["email_status"] == "draft"
    assert "发送时间" in response.content
    assert draft["timing"] == timing
    service.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_delivery_time_keeps_content_for_correction(email_runtime):
    service, model, structured = email_runtime
    structured.ainvoke.return_value = EmailIntent(
        action="send",
        subject="提醒",
        body="开会",
        timing="scheduled",
        send_at=datetime.now(UTC) - timedelta(hours=1),
    )
    service.send.side_effect = EmailScheduleError()
    response, draft = await handle_email_command(
        HumanMessage(content="昨天给我发邮件，主题是提醒，正文是开会"), None, model, config()
    )
    assert response.response_metadata["email_status"] == "invalid_time"
    assert "24小时" in response.content
    assert draft["subject"] == "提醒" and draft["body"] == "开会"
    service.send.side_effect = None
    target = datetime.now(UTC) + timedelta(minutes=10)
    structured.ainvoke.return_value = EmailIntent(
        action="send", subject="提醒", body="开会", timing="scheduled", send_at=target
    )
    service.send.return_value = EmailSendResult(status="scheduled", send_at=target)
    response, _ = await handle_email_command(
        HumanMessage(content="改成十分钟后"), draft, model, config()
    )
    assert response.response_metadata["email_status"] == "scheduled"


@pytest.mark.asyncio
async def test_delivery_time_survives_missing_body_followup(email_runtime):
    service, model, structured = email_runtime
    target = datetime.now(UTC) + timedelta(hours=1)
    structured.ainvoke.return_value = EmailIntent(
        action="draft", subject="提醒", timing="scheduled", send_at=target
    )
    _, draft = await handle_email_command(
        HumanMessage(content="一小时后给我发邮件，主题是提醒"), None, model, config()
    )
    assert draft["send_at"]
    structured.ainvoke.return_value = EmailIntent(
        action="send", subject="提醒", body="开会", timing="scheduled", send_at=target
    )
    service.send.return_value = EmailSendResult(status="scheduled", send_at=target)
    response, _ = await handle_email_command(
        HumanMessage(content="正文是开会"), draft, model, config()
    )
    assert response.response_metadata["email_status"] == "scheduled"
    prompt = json.loads(structured.ainvoke.await_args.args[0][1].content)
    assert datetime.fromisoformat(prompt["pending"]["send_at"]) == target


@pytest.mark.asyncio
@pytest.mark.parametrize("clear_time", [False, True])
async def test_pending_delivery_time_cannot_be_silently_dropped(email_runtime, clear_time):
    service, model, structured = email_runtime
    target = datetime.now(UTC) + timedelta(hours=1)
    structured.ainvoke.return_value = EmailIntent(
        action="draft", subject="提醒", timing="scheduled", send_at=target
    )
    _, draft = await handle_email_command(
        HumanMessage(content="一小时后给我发邮件，主题是提醒"), None, model, config()
    )
    structured.ainvoke.return_value = EmailIntent(
        action="send", subject="提醒", body="开会", clear_send_at=clear_time
    )
    if not clear_time:
        service.send.return_value = EmailSendResult(status="scheduled", send_at=target)
    response, _ = await handle_email_command(
        HumanMessage(content="正文是开会，立即发送" if clear_time else "正文是开会"),
        draft,
        model,
        config(),
    )
    assert response.response_metadata["email_status"] == ("sent" if clear_time else "scheduled")
    assert service.send.await_args.kwargs == ({} if clear_time else {"send_at": target})
