import pytest
from pydantic import SecretStr

from core.settings import Settings


def configured():
    return Settings(
        _env_file=None,
        AUTH_SECRET=SecretStr("trusted"),
        APP_TOKEN_SECRET=SecretStr("a" * 32),
        DASHSCOPE_API_KEY=SecretStr("key"),
        QINIU_ACCESS_KEY=SecretStr("ak"),
        QINIU_SECRET_KEY=SecretStr("sk"),
        QINIU_BUCKET_NAME="bucket",
        QINIU_PUBLIC_BASE_URL="https://cdn.example",
    )


def test_video_disabled_by_default_and_valid_configuration(monkeypatch):
    monkeypatch.delenv("VIDEO_ENABLED", raising=False)
    settings = configured()
    assert settings.VIDEO_ENABLED is False
    settings.require_video_configuration()
    settings.VIDEO_API_BASE_URL = "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1"
    settings.require_video_configuration()


@pytest.mark.parametrize(
    "name",
    [
        "AUTH_SECRET",
        "APP_TOKEN_SECRET",
        "DASHSCOPE_API_KEY",
        "QINIU_ACCESS_KEY",
        "QINIU_SECRET_KEY",
        "QINIU_BUCKET_NAME",
        "QINIU_PUBLIC_BASE_URL",
    ],
)
def test_missing_required_settings_fail_closed(name):
    settings = configured()
    setattr(settings, name, None)
    with pytest.raises(ValueError, match=name):
        settings.require_video_configuration()


@pytest.mark.parametrize(
    "url",
    [
        "http://dashscope.aliyuncs.com/api/v1",
        "https://evil.test/api/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope.aliyuncs.com/api/v1?api_key=secret",
        "https://user:password@dashscope.aliyuncs.com/api/v1",
    ],
)
def test_invalid_endpoint_is_rejected(url):
    settings = configured()
    settings.VIDEO_API_BASE_URL = url
    with pytest.raises(ValueError, match="VIDEO_API_BASE_URL"):
        settings.require_video_configuration()


def test_nacos_video_settings():
    from service.nacos import apply_remote_settings

    settings = configured()
    apply_remote_settings(
        settings,
        "video:\n  enabled: true\n  api_base_url: https://workspace.cn-beijing.maas.aliyuncs.com/api/v1\n",
    )
    assert settings.VIDEO_ENABLED is True
    assert settings.VIDEO_API_BASE_URL == "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1"
