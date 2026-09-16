"""Tests for the Alibaba Cloud Bailian TTS provider."""

import base64
import os
from unittest.mock import Mock, patch

import pytest

from voice.providers.alibaba_tts import AlibabaTTS


def tts_response(audio: dict[str, str]) -> Mock:
    response = Mock()
    response.json.return_value = {"output": {"audio": audio}}
    return response


def test_init_with_explicit_configuration():
    tts = AlibabaTTS(
        api_key="test-key",
        base_url="https://example.com/api/v1/",
        voice="CustomVoice",
        model="custom-tts",
        language_type="Chinese",
    )
    assert tts.api_key == "test-key"
    assert tts.base_url == "https://example.com/api/v1"
    assert tts.voice == "CustomVoice"
    assert tts.model == "custom-tts"
    assert tts.language_type == "Chinese"


def test_init_requires_dashscope_key():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
            AlibabaTTS()


def test_validate_text_too_short():
    tts = AlibabaTTS(api_key="test-key")
    assert tts._validate_and_prepare_text("ab") is None


def test_validate_text_too_long_is_truncated():
    tts = AlibabaTTS(api_key="test-key")
    result = tts._validate_and_prepare_text("a" * 700)
    assert result is not None
    assert len(result) == 600


def test_generate_downloads_wav_from_bailian_response():
    response = tts_response({"url": "https://example.com/generated.wav"})
    download = Mock(content=b"fake wav data")
    with (
        patch("voice.providers.alibaba_tts.httpx.post", return_value=response) as post,
        patch("voice.providers.alibaba_tts.httpx.get", return_value=download) as get,
    ):
        result = AlibabaTTS(api_key="test-key").generate("你好，欢迎体验百炼语音")

    assert result == b"fake wav data"
    response.raise_for_status.assert_called_once_with()
    download.raise_for_status.assert_called_once_with()
    assert post.call_args.args[0].endswith("/api/v1/services/aigc/multimodal-generation/generation")
    request = post.call_args.kwargs
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["json"] == {
        "model": "qwen3-tts-flash",
        "input": {
            "text": "你好，欢迎体验百炼语音",
            "voice": "Cherry",
            "language_type": "Auto",
        },
    }
    get.assert_called_once_with(
        "https://example.com/generated.wav",
        timeout=AlibabaTTS.REQUEST_TIMEOUT,
        follow_redirects=True,
    )


def test_generate_accepts_embedded_base64_audio():
    encoded = base64.b64encode(b"embedded wav data").decode("ascii")
    response = tts_response({"data": f"data:audio/wav;base64,{encoded}"})
    with (
        patch("voice.providers.alibaba_tts.httpx.post", return_value=response),
        patch("voice.providers.alibaba_tts.httpx.get") as get,
    ):
        result = AlibabaTTS(api_key="test-key").generate("Hello Bailian")
    assert result == b"embedded wav data"
    get.assert_not_called()


def test_generate_api_error_returns_none():
    with patch("voice.providers.alibaba_tts.httpx.post", side_effect=RuntimeError("API error")):
        result = AlibabaTTS(api_key="test-key").generate("Hello Bailian")
    assert result is None


def test_audio_format_is_wav():
    assert AlibabaTTS(api_key="test-key").get_format() == "audio/wav"
