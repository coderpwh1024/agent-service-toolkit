from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from schema.video import VideoCreate, VideoEdit
from video.providers import (
    HappyHorseClient,
    QiniuVideoStorage,
    VideoProviderError,
    VideoStorageError,
)


def config():
    return SimpleNamespace(
        DASHSCOPE_API_KEY=SecretStr("secret-api-key"),
        VIDEO_API_BASE_URL="https://workspace.cn-beijing.maas.aliyuncs.com/api/v1",
        QINIU_ACCESS_KEY=SecretStr("ak"),
        QINIU_SECRET_KEY=SecretStr("sk"),
        QINIU_BUCKET_NAME="videos",
        QINIU_PUBLIC_BASE_URL="https://cdn.example",
    )


@pytest.mark.asyncio
async def test_create_and_query_follow_official_wire_contract():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer secret-api-key"
        if request.method == "POST":
            import json

            assert request.headers["X-DashScope-Async"] == "enable"
            assert request.url.path.endswith("/services/aigc/video-generation/video-synthesis")
            assert json.loads(request.content) == {
                "model": "happyhorse-1.1-t2v",
                "input": {"prompt": "horse"},
                "parameters": {
                    "resolution": "1080P",
                    "ratio": "16:9",
                    "duration": 5,
                    "watermark": False,
                },
            }
        else:
            assert request.url.path == "/api/v1/tasks/task-123"
        return httpx.Response(
            200, json={"output": {"task_id": "task-123", "task_status": "PENDING"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = HappyHorseClient(client, config())
        await provider.create(
            VideoCreate(prompt="horse", title="海边", cover_url="https://cdn.example/cover.jpg")
        )
        await provider.query("task-123")


@pytest.mark.asyncio
async def test_edit_follows_official_wire_contract_without_metadata_leakage():
    import json

    body = VideoEdit(
        source_record_id=123,
        source_url="https://cdn.example/video.mp4",
        prompt="水墨风格",
        audio_setting="origin",
        duration=10,
        ratio="16:9",
        title="水墨海边",
        cover_url="https://cdn.example/ink.jpg",
    )

    def handler(request):
        assert request.headers["X-DashScope-Async"] == "enable"
        assert json.loads(request.content) == {
            "model": "happyhorse-1.0-video-edit",
            "input": {
                "prompt": "水墨风格",
                "media": [{"type": "video", "url": "https://cdn.example/video.mp4"}],
            },
            "parameters": {"resolution": "1080P", "audio_setting": "origin", "watermark": False},
        }
        return httpx.Response(
            200, json={"output": {"task_id": "edit-123", "task_status": "PENDING"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await HappyHorseClient(client, config()).create(body)
        assert result.output.task_id == "edit-123"


@pytest.mark.parametrize("model", [VideoCreate, VideoEdit])
@pytest.mark.parametrize(
    "metadata",
    [
        {"title": ""},
        {"title": " \t\n"},
        {"title": "字" * 201},
        {"cover_url": ""},
        {"cover_url": "not-a-url"},
        {"cover_url": "ftp://cdn.example/cover.jpg"},
        {"cover_url": "https://cdn.example/" + "a" * 2048},
    ],
)
def test_video_metadata_validation(model, metadata):
    params = {"prompt": "海边"}
    if model is VideoEdit:
        params.update(
            source_record_id=1, source_url="https://cdn.example/v.mp4", duration=5, ratio="16:9"
        )
    with pytest.raises(ValidationError):
        model.model_validate(params | metadata)


def test_video_metadata_is_optional_and_normalizes_title():
    assert VideoCreate(prompt="海边").title is None
    assert VideoCreate(prompt="海边", title=None, cover_url=None).cover_url is None
    body = VideoCreate(prompt="海边", title=" 海边日落 ", cover_url="http://cdn.example/c.jpg")
    assert body.title == "海边日落"
    assert str(body.cover_url) == "http://cdn.example/c.jpg"
    assert VideoCreate(prompt="海边", title="字" * 200).title == "字" * 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "http_status, uncertain", [(400, False), (429, False), (500, True), (408, True)]
)
async def test_provider_error_classification(http_status, uncertain):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(http_status))
    ) as client:
        with pytest.raises(VideoProviderError) as caught:
            await HappyHorseClient(client, config()).create(VideoCreate(prompt="horse"))
        assert caught.value.uncertain == uncertain


@pytest.mark.asyncio
async def test_timeout_and_malformed_reply_do_not_enable_resubmission():
    def timeout(request):
        raise httpx.ReadTimeout("signed private URL", request=request)

    for handler in [timeout, lambda _: httpx.Response(200, json={"output": {}})]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(VideoProviderError) as caught:
                await HappyHorseClient(client, config()).create(VideoCreate(prompt="horse"))
            assert caught.value.uncertain


@pytest.mark.asyncio
async def test_mismatched_task_id_is_rejected():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, json={"output": {"task_id": "other", "task_status": "PENDING"}}
            )
        )
    ) as client:
        with pytest.raises(VideoProviderError):
            await HappyHorseClient(client, config()).query("task-123")


@pytest.mark.asyncio
async def test_qiniu_fixed_key_recovers_success_after_database_write_loss():
    storage = QiniuVideoStorage(config())
    storage.manager = Mock()
    storage.manager.stat.side_effect = [
        (None, SimpleNamespace(status_code=612)),
        ({"hash": "hash", "fsize": 1024}, SimpleNamespace(status_code=200)),
    ]
    storage.manager.fetch.return_value = (
        {"key": "videos/happyhorse/task-123.mp4", "hash": "hash", "fsize": 1024},
        SimpleNamespace(status_code=200),
    )
    source = "https://dashscope-result.oss-cn-beijing.aliyuncs.com/file.mp4?signature=private"
    first = await storage.archive("task-123", source)
    second = await storage.archive("task-123", source)
    assert (
        first
        == second
        == ("videos/happyhorse/task-123.mp4", "https://cdn.example/videos/happyhorse/task-123.mp4")
    )
    assert storage.manager.fetch.call_count == 1


@pytest.mark.asyncio
async def test_cover_is_a_separate_archived_jpeg_and_recovers_after_database_write_loss():
    storage = QiniuVideoStorage(config())
    storage.manager = Mock()
    saved = {
        "key": "videos/happyhorse/task-123.jpg",
        "hash": "cover-hash",
        "fsize": 1024,
        "mimeType": "image/jpeg",
    }
    storage.manager.stat.side_effect = [
        (None, SimpleNamespace(status_code=612)),
        (saved, SimpleNamespace(status_code=200)),
    ]
    storage.manager.fetch.return_value = (saved, SimpleNamespace(status_code=200))
    first = await storage.archive_cover("task-123", "videos/happyhorse/task-123.mp4")
    second = await storage.archive_cover("task-123", "videos/happyhorse/task-123.mp4")
    assert (
        first
        == second
        == ("videos/happyhorse/task-123.jpg", "https://cdn.example/videos/happyhorse/task-123.jpg")
    )
    storage.manager.fetch.assert_called_once_with(
        "https://cdn.example/videos/happyhorse/task-123.mp4?vframe/jpg/offset/1",
        "videos",
        "videos/happyhorse/task-123.jpg",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("mime_type", ["text/html", "video/mp4", None])
async def test_cover_does_not_return_a_url_for_non_image_objects(existing, mime_type):
    storage = QiniuVideoStorage(config())
    storage.manager = Mock()
    result = {
        "key": "videos/happyhorse/task-123.jpg",
        "hash": "hash",
        "fsize": 100,
        "mimeType": mime_type,
    }
    storage.manager.stat.return_value = (
        (result, SimpleNamespace(status_code=200))
        if existing
        else (None, SimpleNamespace(status_code=612))
    )
    storage.manager.fetch.return_value = (result, SimpleNamespace(status_code=200))
    with pytest.raises(VideoStorageError):
        await storage.archive_cover("task-123", "videos/happyhorse/task-123.mp4")


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [403, 500])
async def test_cover_lookup_error_is_retryable_and_does_not_fetch(status):
    storage = QiniuVideoStorage(config())
    storage.manager = Mock()
    storage.manager.stat.return_value = (None, SimpleNamespace(status_code=status))
    with pytest.raises(VideoStorageError):
        await storage.archive_cover("task-123", "videos/happyhorse/task-123.mp4")
    storage.manager.fetch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source", ["http://127.0.0.1/v", "https://evil.test/v", "https://aliyuncs.com.evil.test/v"]
)
async def test_untrusted_source_is_not_fetched(source):
    storage = QiniuVideoStorage(config())
    storage.manager = Mock()
    with pytest.raises(VideoStorageError):
        await storage.archive("task-123", source)
    storage.manager.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_qiniu_failure_does_not_return_url():
    storage = QiniuVideoStorage(config())
    storage.manager = Mock()
    storage.manager.stat.return_value = (None, SimpleNamespace(status_code=612))
    storage.manager.fetch.return_value = (None, SimpleNamespace(status_code=500))
    with pytest.raises(VideoStorageError):
        await storage.archive("task-123", "https://result.oss-cn-beijing.aliyuncs.com/v")


@pytest.mark.parametrize(
    "changes",
    [
        {"prompt": " "},
        {"prompt": "中" * 2501},
        {"duration": 2},
        {"duration": 16},
        {"duration": 5.5},
        {"seed": -1},
        {"seed": 2147483648},
        {"resolution": "4K"},
        {"model": "happyhorse-1.0-t2v", "resolution": "480P"},
        {"user_id": 99},
    ],
)
def test_request_boundaries(changes):
    with pytest.raises(ValidationError):
        VideoCreate.model_validate({"prompt": "horse", **changes})
