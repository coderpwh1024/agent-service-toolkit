"""HappyHorse asynchronous API and Qiniu server-side video archival."""

import asyncio
import logging
from urllib.parse import quote, urlsplit

import httpx
from pydantic import ValidationError
from qiniu import Auth, BucketManager

from core.settings import Settings
from schema.video import ProviderResult, VideoCreate

logger = logging.getLogger(__name__)


class VideoProviderError(RuntimeError):
    def __init__(self, *, uncertain: bool = False):
        super().__init__("阿里云视频接口暂不可用")
        self.uncertain = uncertain


class VideoStorageError(RuntimeError):
    pass


class HappyHorseClient:
    def __init__(self, client: httpx.AsyncClient, config: Settings):
        self.client = client
        self.base_url = config.VIDEO_API_BASE_URL.rstrip("/")
        if config.DASHSCOPE_API_KEY is None:
            raise ValueError("视频生成需要 DASHSCOPE_API_KEY")
        self.headers = {"Authorization": f"Bearer {config.DASHSCOPE_API_KEY.get_secret_value()}"}

    async def create(self, body: VideoCreate) -> ProviderResult:
        logger.info(
            "阿里云视频创建入参：模型=%s 分辨率=%s 时长=%s 提示词字数=%s",
            body.model,
            body.resolution,
            body.duration,
            len(body.prompt),
        )
        return await self._request(
            "POST",
            "/services/aigc/video-generation/video-synthesis",
            json=body.provider_payload(),
        )

    async def query(self, task_id: str) -> ProviderResult:
        logger.info("阿里云视频查询入参：任务ID=%s", task_id)
        result = await self._request("GET", f"/tasks/{quote(task_id, safe='')}")
        if result.output.task_id != task_id:
            raise VideoProviderError()
        return result

    async def _request(
        self, method: str, path: str, *, json: dict[str, object] | None = None
    ) -> ProviderResult:
        headers = dict(self.headers)
        if method == "POST":
            headers["X-DashScope-Async"] = "enable"
        try:
            response = await self.client.request(
                method,
                self.base_url + path,
                headers=headers,
                json=json,
            )
        except httpx.RequestError as exc:
            logger.warning("阿里云视频调用异常：操作=%s 异常类型=%s", method, type(exc).__name__)
            raise VideoProviderError(uncertain=method == "POST") from None
        if not response.is_success:
            logger.warning("阿里云视频调用失败：操作=%s HTTP状态=%s", method, response.status_code)
            raise VideoProviderError(
                uncertain=method == "POST"
                and (response.status_code >= 500 or response.status_code == 408)
            )
        try:
            result = ProviderResult.model_validate(response.json())
        except (ValueError, ValidationError):
            logger.warning("阿里云视频出参格式异常：操作=%s", method)
            raise VideoProviderError(uncertain=method == "POST") from None
        logger.info(
            "阿里云视频出参：任务ID=%s 状态=%s 已返回视频地址=%s",
            result.output.task_id,
            result.output.task_status,
            result.output.video_url is not None,
        )
        return result


class QiniuVideoStorage:
    def __init__(self, config: Settings):
        if config.QINIU_ACCESS_KEY is None or config.QINIU_SECRET_KEY is None:
            raise ValueError("视频转存需要七牛云密钥")
        self.manager = BucketManager(
            Auth(
                config.QINIU_ACCESS_KEY.get_secret_value(),
                config.QINIU_SECRET_KEY.get_secret_value(),
            )
        )
        self.bucket = config.QINIU_BUCKET_NAME
        self.base_url = (config.QINIU_PUBLIC_BASE_URL or "").rstrip("/")

    async def archive(self, task_id: str, source_url: str) -> tuple[str, str]:
        parsed = urlsplit(source_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".aliyuncs.com")
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            raise VideoStorageError("阿里云视频来源地址不受信任")
        object_key = f"videos/happyhorse/{task_id}.mp4"
        logger.info("七牛云视频转存入参：任务ID=%s 对象键=%s", task_id, object_key)
        try:
            await asyncio.to_thread(self._archive, source_url, object_key)
        except VideoStorageError:
            raise
        except Exception as exc:
            logger.warning("七牛云视频转存异常：任务ID=%s 异常类型=%s", task_id, type(exc).__name__)
            raise VideoStorageError("七牛云视频转存失败") from None
        url = f"{self.base_url}/{quote(object_key, safe='/')}"
        logger.info("七牛云视频转存出参：任务ID=%s 对象键=%s 转存成功=true", task_id, object_key)
        return object_key, url

    def _archive(self, source_url: str, object_key: str) -> None:
        result, response = self.manager.stat(self.bucket, object_key)
        if response.status_code == 200:
            if not isinstance(result, dict) or not result.get("hash") or not result.get("fsize"):
                raise VideoStorageError("七牛云已有对象校验失败")
            return
        if response.status_code != 612:
            raise VideoStorageError("七牛云对象查询失败")
        result, response = self.manager.fetch(source_url, self.bucket, object_key)
        if (
            response.status_code != 200
            or not isinstance(result, dict)
            or result.get("key") != object_key
            or not result.get("hash")
            or not result.get("fsize")
        ):
            raise VideoStorageError("七牛云未确认视频转存成功")
