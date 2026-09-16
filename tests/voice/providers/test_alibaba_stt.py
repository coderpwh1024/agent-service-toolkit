"""Tests for the Alibaba Cloud Bailian STT provider."""

import base64
import os
from unittest.mock import Mock, patch

import pytest

from voice.providers.alibaba_stt import AlibabaSTT


def asr_response(content: str = " transcribed text ") -> Mock:
    response = Mock()
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


def test_init_with_explicit_configuration():
    stt = AlibabaSTT(
        api_key="test-key",
        base_url="https://example.com/v1/",
        model="custom-asr",
    )
    assert stt.api_key == "test-key"
    assert stt.base_url == "https://example.com/v1"
    assert stt.model == "custom-asr"


def test_init_requires_dashscope_key():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="DASHSCOPE_API_KEY"):
            AlibabaSTT()


def test_transcribe_sends_base64_audio_to_bailian(mock_audio_file):
    response = asr_response()
    with patch("voice.providers.alibaba_stt.httpx.post", return_value=response) as post:
        result = AlibabaSTT(api_key="test-key").transcribe(mock_audio_file)

    assert result == "transcribed text"
    response.raise_for_status.assert_called_once_with()
    assert post.call_args.args[0].endswith("/compatible-mode/v1/chat/completions")
    request = post.call_args.kwargs
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["json"]["model"] == "qwen3-asr-flash"
    audio_data = request["json"]["messages"][0]["content"][0]["input_audio"]["data"]
    prefix, encoded = audio_data.split(",", maxsplit=1)
    assert prefix == "data:audio/wav;base64"
    assert base64.b64decode(encoded) == b"fake audio bytes"


def test_transcribe_reads_from_start_of_audio_file(mock_audio_file):
    mock_audio_file.seek(5)
    response = asr_response("ok")
    with patch("voice.providers.alibaba_stt.httpx.post", return_value=response) as post:
        AlibabaSTT(api_key="test-key").transcribe(mock_audio_file)

    audio_data = post.call_args.kwargs["json"]["messages"][0]["content"][0]["input_audio"]["data"]
    assert base64.b64decode(audio_data.split(",", maxsplit=1)[1]) == b"fake audio bytes"


def test_transcribe_rejects_audio_larger_than_base64_limit(mock_audio_file, monkeypatch):
    monkeypatch.setattr(AlibabaSTT, "MAX_AUDIO_BYTES", 3)
    with patch("voice.providers.alibaba_stt.httpx.post") as post:
        result = AlibabaSTT(api_key="test-key").transcribe(mock_audio_file)
    assert result == ""
    post.assert_not_called()


def test_transcribe_api_error_returns_empty_string(mock_audio_file):
    with patch("voice.providers.alibaba_stt.httpx.post", side_effect=RuntimeError("API error")):
        result = AlibabaSTT(api_key="test-key").transcribe(mock_audio_file)
    assert result == ""
