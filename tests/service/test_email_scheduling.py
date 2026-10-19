import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from psycopg import OperationalError
from pydantic import SecretStr, ValidationError

from core import settings
from schema import UserProfile
from schema.email_skill import (
    EmailContent,
    EmailScheduleError,
    EmailSendRequest,
    ScheduledEmail,
    validate_send_at,
)
from service.access import create_access_token
from skills import email

NOW = datetime(2026, 10, 19, 8, tzinfo=UTC)
CONTENT = EmailContent(subject="会议提醒", body="明天下午三点开会。")


@pytest.fixture
def scheduled_delivery():
    users = Mock(
        get_by_id=AsyncMock(
            return_value=UserProfile(id=7, nickname="测试", email="user@example.com")
        )
    )
    sender = Mock(send_email=AsyncMock())
    redis = Mock(eval=AsyncMock())
    clock = Mock(return_value=NOW)
    service = email.EmailSkillService(users, sender, redis, clock=clock)
    return service, users, sender, redis, clock


@pytest.mark.parametrize(
    "offset", [timedelta(0), timedelta(seconds=-1), timedelta(hours=24, microseconds=1)]
)
def test_schedule_rejects_now_past_and_over_24_hours(offset):
    with pytest.raises(EmailScheduleError):
        validate_send_at(NOW + offset, NOW)


@pytest.mark.parametrize("offset", [timedelta(microseconds=1), timedelta(hours=24)])
def test_schedule_accepts_future_and_inclusive_24_hour_boundary(offset):
    assert validate_send_at(NOW + offset, NOW) == NOW + offset


def test_schedule_compares_instants_across_timezones_and_requires_offset():
    shanghai = timezone(timedelta(hours=8))
    assert validate_send_at(datetime(2026, 10, 19, 17, tzinfo=shanghai), NOW) == NOW + timedelta(
        hours=1
    )
    with pytest.raises(EmailScheduleError):
        validate_send_at(datetime(2026, 10, 19, 17), NOW)
    with pytest.raises(ValidationError):
        EmailSendRequest(
            request_id=uuid4(), subject="主题", body="正文", send_at="2026-10-19T17:00:00"
        )


@pytest.mark.asyncio
async def test_scheduling_stores_job_without_calling_smtp(scheduled_delivery, caplog):
    service, _, sender, redis, _ = scheduled_delivery
    redis.eval.return_value = "reserved"
    with caplog.at_level(logging.INFO):
        result = await service.send("7", "request", CONTENT, send_at=NOW + timedelta(hours=24))
    assert result.status == "scheduled"
    assert result.send_at == NOW + timedelta(hours=24)
    sender.send_email.assert_not_awaited()
    assert "scheduled" in caplog.text
    assert CONTENT.subject not in caplog.text and CONTENT.body not in caplog.text
    assert "user@example.com" not in caplog.text
    args = redis.eval.await_args.args
    payload = ScheduledEmail.model_validate_json(args[8])
    assert payload.user_id == "7" and payload.content == CONTENT
    assert args[-1] == 172800


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [timedelta(seconds=-1), timedelta(hours=25)])
async def test_invalid_schedule_has_no_external_side_effects(scheduled_delivery, offset):
    service, users, sender, redis, _ = scheduled_delivery
    with pytest.raises(EmailScheduleError):
        await service.send("7", "request", CONTENT, send_at=NOW + offset)
    users.get_by_id.assert_not_awaited()
    sender.send_email.assert_not_awaited()
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_is_revalidated_after_user_lookup(scheduled_delivery):
    service, _, sender, redis, clock = scheduled_delivery
    clock.side_effect = [NOW, NOW + timedelta(seconds=2)]
    with pytest.raises(EmailScheduleError):
        await service.send("7", "request", CONTENT, send_at=NOW + timedelta(seconds=1))
    sender.send_email.assert_not_awaited()
    redis.eval.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("send_at", [None, NOW + timedelta(hours=2)])
async def test_replay_of_scheduled_request_never_sends_early(scheduled_delivery, send_at):
    service, _, sender, redis, _ = scheduled_delivery
    original = NOW + timedelta(hours=1)
    redis.eval.return_value = "scheduled:" + original.isoformat()
    result = await service.send("7", "request", CONTENT, send_at=send_at)
    assert result.status == "scheduled" and result.send_at == original
    sender.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_due_job_uses_current_registered_address_after_restart(scheduled_delivery):
    service, users, sender, redis, clock = scheduled_delivery
    clock.return_value = NOW + timedelta(hours=1)
    payload = ScheduledEmail(user_id="7", content=CONTENT, send_at=clock()).model_dump_json()
    digest = hashlib.sha256(b"7:request").hexdigest()
    redis.eval.side_effect = [[digest, payload], 1, "OK", []]
    users.get_by_id.return_value = UserProfile(id=7, nickname="测试", email="updated@example.com")
    restarted = email.EmailSkillService(users, sender, redis, clock=clock)
    assert await restarted.process_due() == 1
    sender.send_email.assert_awaited_once_with("updated@example.com", CONTENT.subject, CONTENT.body)


@pytest.mark.asyncio
async def test_future_job_is_not_dispatched(scheduled_delivery):
    service, _, sender, redis, _ = scheduled_delivery
    redis.eval.return_value = []
    assert await service.process_due() == 0
    sender.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_lost_claim_does_not_send(scheduled_delivery):
    service, _, sender, redis, _ = scheduled_delivery
    payload = ScheduledEmail(user_id="7", content=CONTENT, send_at=NOW).model_dump_json()
    redis.eval.side_effect = [["digest", payload], 0, []]
    assert await service.process_due() == 0
    sender.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_deleted_user_job_is_discarded(scheduled_delivery):
    service, users, sender, redis, _ = scheduled_delivery
    users.get_by_id.return_value = None
    payload = ScheduledEmail(user_id="7", content=CONTENT, send_at=NOW).model_dump_json()
    redis.eval.side_effect = [["digest", payload], 1, []]
    assert await service.process_due() == 1
    sender.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_temporary_user_lookup_failure_leaves_job_unclaimed(scheduled_delivery):
    service, users, sender, redis, _ = scheduled_delivery
    users.get_by_id.side_effect = OperationalError()
    payload = ScheduledEmail(user_id="7", content=CONTENT, send_at=NOW).model_dump_json()
    redis.eval.side_effect = [["digest", payload]]
    with pytest.raises(email.EmailSkillUnavailable):
        await service.process_due()
    assert redis.eval.await_count == 1
    sender.send_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_uncertainty_is_not_retried(scheduled_delivery):
    service, _, sender, redis, _ = scheduled_delivery
    sender.send_email.side_effect = email.EmailDeliveryError()
    payload = ScheduledEmail(user_id="7", content=CONTENT, send_at=NOW).model_dump_json()
    redis.eval.side_effect = [["digest", payload], 1, []]
    assert await service.process_due() == 1
    assert sender.send_email.await_count == 1


@pytest.mark.asyncio
async def test_scheduler_task_can_be_stopped(scheduled_delivery):
    service, _, _, redis, _ = scheduled_delivery
    started = asyncio.Event()

    async def peek(*args):
        started.set()
        return []

    redis.eval.side_effect = peek
    task = asyncio.create_task(service.run_scheduler())
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    "offset,expected",
    [
        (None, "sent"),
        (timedelta(hours=1), "scheduled"),
        (timedelta(hours=25), None),
        (timedelta(seconds=-1), None),
    ],
)
def test_api_optional_send_time_and_server_validation(
    test_client, monkeypatch, scheduled_delivery, offset, expected
):
    service, _, sender, redis, _ = scheduled_delivery
    redis.eval.side_effect = ["reserved", "OK"]
    monkeypatch.setattr(email, "_service", service)
    monkeypatch.setattr(settings, "AUTH_SECRET", SecretStr("admin"))
    monkeypatch.setattr(settings, "APP_TOKEN_SECRET", SecretStr("a" * 48))
    token, _ = create_access_token("7")
    payload = {"request_id": str(uuid4()), **CONTENT.model_dump()}
    if offset is not None:
        payload["send_at"] = (NOW + offset).isoformat()
    response = test_client.post(
        "/skills/email/send", json=payload, headers={"Authorization": "Bearer " + token}
    )
    assert response.status_code == (200 if expected else 422), response.text
    if expected:
        assert response.json()["data"]["status"] == expected
    assert sender.send_email.await_count == int(expected == "sent")
