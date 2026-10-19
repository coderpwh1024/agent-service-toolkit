import asyncio
import importlib.util
import io
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from psycopg import OperationalError
from pydantic import SecretStr, ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError

from core import settings
from schema import UserProfile
from schema.email_skill import EmailContent
from service.access import create_access_token
from service.email_auth import SMTPEmailSender
from service.email_skill import email_skill_lifespan
from skills import email


@pytest.fixture
def delivery():
    users = Mock(
        get_by_id=AsyncMock(
            return_value=UserProfile(id=7, nickname="测试", email="user@example.com")
        )
    )
    sender = Mock(send_email=AsyncMock())
    redis = Mock(eval=AsyncMock(side_effect=["reserved", "OK"]))
    return email.EmailSkillService(users, sender, redis), users, sender, redis


@pytest.mark.asyncio
async def test_delivery_uses_registered_address_and_logs_no_content(delivery, caplog):
    service, users, sender, redis = delivery
    with caplog.at_level(logging.INFO):
        result = await service.send(
            "7", "request", EmailContent(subject="私人主题", body="私人正文")
        )
    assert result.status == "sent"
    users.get_by_id.assert_awaited_once_with(7)
    sender.send_email.assert_awaited_once_with("user@example.com", "私人主题", "私人正文")
    assert redis.eval.await_count == 2
    assert "sent" in caplog.text
    assert all(value not in caplog.text for value in ["私人主题", "私人正文", "user@example.com"])


@pytest.mark.asyncio
@pytest.mark.parametrize("state,expected", [("sent", "duplicate"), ("unknown", "unknown")])
async def test_replays_never_send_again(delivery, state, expected):
    service, _, sender, redis = delivery
    redis.eval.side_effect = [state]
    assert (
        await service.send("7", "request", EmailContent(subject="你好", body="正文"))
    ).status == expected
    sender.send_email.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["alice", "0", "-1", str(2**63), "７"])
async def test_unregistered_identity_cannot_send(delivery, user_id):
    service, users, sender, redis = delivery
    with pytest.raises(email.EmailSkillUnavailable):
        await service.send(user_id, "request", EmailContent(subject="你好", body="正文"))
    users.get_by_id.assert_not_awaited()
    sender.send_email.assert_not_awaited()
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_user_cannot_send(delivery):
    service, users, sender, _ = delivery
    users.get_by_id.return_value = None
    with pytest.raises(email.EmailSkillUnavailable):
        await service.send("7", "request", EmailContent(subject="你好", body="正文"))
    sender.send_email.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [email.EmailDeliveryError(), RedisConnectionError()])
async def test_delivery_failures_return_unknown_without_retry(delivery, failure):
    service, _, sender, redis = delivery
    if isinstance(failure, email.EmailDeliveryError):
        sender.send_email.side_effect = failure
    else:
        redis.eval.side_effect = ["reserved", failure]
    result = await service.send("7", "request", EmailContent(subject="你好", body="正文"))
    assert result.status == "unknown"
    assert sender.send_email.await_count == 1


@pytest.mark.asyncio
async def test_cancellation_preserves_unknown_reservation(delivery):
    service, _, sender, redis = delivery
    sender.send_email.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await service.send("7", "request", EmailContent(subject="你好", body="正文"))
    assert redis.eval.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["limited", RedisConnectionError()])
async def test_limit_or_redis_failure_prevents_smtp(delivery, state):
    service, _, sender, redis = delivery
    redis.eval.side_effect = [state]
    with pytest.raises((email.EmailSkillRateLimited, email.EmailSkillUnavailable)):
        await service.send("7", "request", EmailContent(subject="你好", body="正文"))
    sender.send_email.assert_not_awaited()


@pytest.mark.parametrize(
    "subject,body",
    [
        ("", "正文"),
        ("主题", " "),
        ("主题\r\nBcc: x@example.com", "正文"),
        ("x" * 201, "正文"),
        ("主题", "\0"),
    ],
)
def test_content_validation(subject, body):
    with pytest.raises(ValidationError):
        EmailContent(subject=subject, body=body)


@pytest.mark.asyncio
async def test_smtp_message_is_valid_utf8_with_required_headers(monkeypatch):
    sender = SMTPEmailSender(
        SimpleNamespace(
            SMTP_HOST="smtp.qq.com",
            SMTP_PORT=465,
            SMTP_USERNAME="sender@example.com",
            SMTP_PASSWORD=SecretStr("placeholder"),
            SMTP_FROM_EMAIL="sender@example.com",
            SMTP_USE_TLS=False,
            SMTP_USE_SSL=True,
        )
    )
    send = Mock()
    monkeypatch.setattr(sender, "_send", send)
    await sender.send_email("user@example.com", "会议提醒", "你好，\n明天下午三点开会。")
    message = send.call_args.args[0]
    assert str(message["Subject"]) == "会议提醒"
    assert message["To"] == "user@example.com"
    assert message["From"] == "sender@example.com"
    assert message["Date"] and message["Message-ID"]
    assert message.get_content_charset() == "utf-8"
    assert "明天下午三点开会。" in message.get_content()
    assert message["Bcc"] is None


def test_endpoint_binds_token_user_and_rejects_spoofed_recipient(
    test_client, monkeypatch, delivery
):
    service, _, sender, _ = delivery
    monkeypatch.setattr(email, "_service", service)
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("admin"))
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("a" * 48))
    token, _ = create_access_token("7")
    headers = {"Authorization": "Bearer " + token}
    payload = {"request_id": str(uuid4()), "subject": "主题", "body": "正文"}
    assert test_client.post("/skills/email/send", json=payload).status_code == 401
    assert (
        test_client.post(
            "/skills/email/send", json=payload, headers={"Authorization": "Bearer admin"}
        ).status_code
        == 403
    )
    assert (
        test_client.post(
            "/skills/email/send", json={**payload, "to": "other@example.com"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        test_client.post(
            "/skills/email/send", json={**payload, "user_id": "8"}, headers=headers
        ).status_code
        == 422
    )
    response = test_client.post("/skills/email/send", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "sent"
    sender.send_email.assert_awaited_once_with("user@example.com", "主题", "正文")


def test_bundled_script_calls_authenticated_endpoint_without_recipient(monkeypatch, capsys):
    path = Path("skills/send-current-user-email/scripts/send_email.py")
    spec = importlib.util.spec_from_file_location("skill_send_email", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("AGENT_SERVICE_URL", "https://service.example.com")
    monkeypatch.setenv("AGENT_USER_ACCESS_TOKEN", "signed-in-token")
    monkeypatch.setattr(
        module.sys,
        "stdin",
        io.StringIO('{"request_id":"' + str(uuid4()) + '","subject":"主题","body":"正文"}'),
    )
    response = io.StringIO('{"data":{"status":"sent"}}')
    opener = Mock(open=Mock(return_value=response))
    monkeypatch.setattr(module, "build_opener", Mock(return_value=opener))
    assert module.main() == 0
    request = opener.open.call_args.args[0]
    assert request.full_url == "https://service.example.com/skills/email/send"
    assert request.get_header("Authorization") == "Bearer signed-in-token"
    assert "sent" in capsys.readouterr().out


def test_client_cannot_forge_skill_identity(test_client, mock_agent, monkeypatch):
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("admin"))
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", None)
    response = test_client.post(
        "/invoke",
        headers={"Authorization": "Bearer admin"},
        json={"message": "给我发邮件", "agent_config": {"authenticated_user_id": "7"}},
    )
    assert response.status_code == 422
    mock_agent.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("dependency", ["postgres", "redis"])
def test_infrastructure_failure_returns_service_unavailable(
    test_client, monkeypatch, delivery, dependency
):
    service, users, sender, redis = delivery
    monkeypatch.setattr(email, "_service", service)
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("admin"))
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("a" * 48))
    if dependency == "postgres":
        users.get_by_id.side_effect = OperationalError("private infrastructure details")
    else:
        redis.eval.side_effect = RedisConnectionError("private infrastructure details")
    token, _ = create_access_token("7")
    response = test_client.post(
        "/skills/email/send",
        headers={"Authorization": "Bearer " + token},
        json={"request_id": str(uuid4()), "subject": "主题", "body": "正文"},
    )
    assert response.status_code == 503
    assert "private infrastructure details" not in response.text
    sender.send_email.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("nacos_enabled,encrypted", [(False, True), (True, False)])
async def test_lifespan_rejects_missing_nacos_or_unencrypted_smtp(
    monkeypatch, nacos_enabled, encrypted
):
    from core.skills import EMAIL_SKILL_NAME, EmailSkill

    monkeypatch.setattr(settings, "EMAIL_SKILL_ENABLED", True)
    monkeypatch.setattr(settings, "NACOS_ENABLED", nacos_enabled)
    monkeypatch.setattr(
        settings, "_email_skill", EmailSkill(EMAIL_SKILL_NAME, "邮件", "workflow", "digest")
    )
    monkeypatch.setattr(settings, "SMTP_USE_SSL", encrypted)
    monkeypatch.setattr(settings, "SMTP_USE_TLS", False)
    app = FastAPI()
    app.state.email_auth_service = Mock()
    with pytest.raises(ValueError):
        async with email_skill_lifespan(app):
            pytest.fail("Invalid email runtime must not start")
    assert email.get_email_skill_service() is None


@pytest.mark.asyncio
async def test_lifespan_shares_existing_resources_and_clears_service(monkeypatch):
    from core.skills import EMAIL_SKILL_NAME, EmailSkill

    monkeypatch.setattr(settings, "EMAIL_SKILL_ENABLED", True)
    monkeypatch.setattr(settings, "NACOS_ENABLED", True)
    monkeypatch.setattr(
        settings, "_email_skill", EmailSkill(EMAIL_SKILL_NAME, "邮件", "workflow", "digest")
    )
    monkeypatch.setattr(settings, "SMTP_USE_SSL", True)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "sender@example.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", SecretStr("placeholder"))
    app = FastAPI()
    auth = Mock()
    app.state.email_auth_service = auth
    async with email_skill_lifespan(app):
        service = email.get_email_skill_service()
        assert service.users is auth.users
        assert service.sender is auth.sender
        assert service.redis is auth.codes.redis
    assert email.get_email_skill_service() is None
