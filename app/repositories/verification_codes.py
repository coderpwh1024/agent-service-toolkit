import hashlib
import json
from collections.abc import Awaitable
from typing import Any, Protocol

VERIFICATION_CODE_TTL_SECONDS = 180


EMAIL_SEND_LIMIT = 6


EMAIL_SEND_WINDOW_SECONDS = 86_400


MAX_VERIFICATION_ATTEMPTS = 5


_RESERVE_CODE_SCRIPT = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current >= tonumber(ARGV[1]) then
    return {0, redis.call('TTL', KEYS[1])}
end
current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[2])
end
redis.call('HSET', KEYS[2],
    'digest', ARGV[3],
    'profile', ARGV[4],
    'attempts', 0)
redis.call('EXPIRE', KEYS[2], ARGV[5])
return {current, redis.call('TTL', KEYS[1])}
"""


_CONSUME_CODE_SCRIPT = """
local stored = redis.call('HGET', KEYS[1], 'digest')
if not stored then
    return {0, false}
end
if stored == ARGV[1] then
    local profile = redis.call('HGET', KEYS[1], 'profile')
    redis.call('DEL', KEYS[1])
    return {1, profile}
end
local attempts = redis.call('HINCRBY', KEYS[1], 'attempts', 1)
if attempts >= tonumber(ARGV[2]) then
    redis.call('DEL', KEYS[1])
end
return {-1, false}
"""


_DISCARD_CODE_SCRIPT = """
if redis.call('HGET', KEYS[1], 'digest') == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class EmailRateLimitError(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Email verification send limit reached")
        self.retry_after = retry_after


class InvalidVerificationCodeError(Exception):
    pass


class AsyncRedisProtocol(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Awaitable[Any]: ...

    def ping(self) -> Awaitable[bool]: ...

    def aclose(self) -> Awaitable[None]: ...


class VerificationCodeStore:
    def __init__(self, redis: AsyncRedisProtocol):
        self.redis = redis

    @staticmethod
    def _email_key(email: str) -> str:
        return hashlib.sha256(email.encode()).hexdigest()

    async def reserve(
        self, email: str, digest: str, registration_profile: dict[str, str | None]
    ) -> None:
        email_key = self._email_key(email)
        result = await self.redis.eval(
            _RESERVE_CODE_SCRIPT,
            2,
            f"email_auth:send:{email_key}",
            f"email_auth:code:{email_key}",
            EMAIL_SEND_LIMIT,
            EMAIL_SEND_WINDOW_SECONDS,
            digest,
            json.dumps(registration_profile, ensure_ascii=False),
            VERIFICATION_CODE_TTL_SECONDS,
        )
        if int(result[0]) == 0:
            raise EmailRateLimitError(max(int(result[1]), 1))

    async def consume(self, email: str, digest: str) -> dict[str, str | None]:
        result = await self.redis.eval(
            _CONSUME_CODE_SCRIPT,
            1,
            f"email_auth:code:{self._email_key(email)}",
            digest,
            MAX_VERIFICATION_ATTEMPTS,
        )
        if int(result[0]) != 1 or not result[1]:
            raise InvalidVerificationCodeError
        profile = json.loads(result[1])
        if not isinstance(profile, dict):
            raise InvalidVerificationCodeError
        return profile

    async def discard(self, email: str, digest: str) -> None:
        await self.redis.eval(
            _DISCARD_CODE_SCRIPT,
            1,
            f"email_auth:code:{self._email_key(email)}",
            digest,
        )

    async def ping(self) -> bool:
        return bool(await self.redis.ping())

    async def close(self) -> None:
        await self.redis.aclose()
