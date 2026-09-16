"""Tests for the speech-to-text factory."""

import os
from unittest.mock import patch

import pytest

from voice.stt import SpeechToText


def test_init_with_alibaba_provider():
    stt = SpeechToText(provider="alibaba", api_key="test-key")
    assert stt.provider == "alibaba"


def test_init_with_invalid_provider():
    with pytest.raises(ValueError, match="Unknown STT provider: invalid"):
        SpeechToText(provider="invalid", api_key="test-key")  # type: ignore[arg-type]


def test_from_env_provider_and_key_not_set():
    with patch.dict(os.environ, {}, clear=True):
        assert SpeechToText.from_env() is None


def test_openai_key_does_not_enable_voice_input():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "legacy-key"}, clear=True):
        assert SpeechToText.from_env() is None


def test_from_env_auto_enables_alibaba_with_dashscope_key():
    with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}, clear=True):
        stt = SpeechToText.from_env()
    assert stt is not None
    assert stt.provider == "alibaba"


def test_from_env_valid_explicit_provider():
    with patch.dict(
        os.environ,
        {"VOICE_STT_PROVIDER": "alibaba", "DASHSCOPE_API_KEY": "test-key"},
        clear=True,
    ):
        stt = SpeechToText.from_env()
    assert stt is not None
    assert stt.provider == "alibaba"


def test_from_env_empty_provider_disables_stt():
    with patch.dict(
        os.environ,
        {"VOICE_STT_PROVIDER": "", "DASHSCOPE_API_KEY": "test-key"},
        clear=True,
    ):
        assert SpeechToText.from_env() is None


def test_from_env_invalid_provider_returns_none():
    with patch.dict(
        os.environ,
        {"VOICE_STT_PROVIDER": "invalid", "DASHSCOPE_API_KEY": "test-key"},
        clear=True,
    ):
        assert SpeechToText.from_env() is None


def test_api_key_parameter_takes_precedence():
    with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key"}, clear=True):
        stt = SpeechToText(provider="alibaba", api_key="param-key")
    assert stt._provider.api_key == "param-key"


def test_api_key_is_loaded_from_dashscope_environment():
    with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key"}, clear=True):
        stt = SpeechToText(provider="alibaba")
    assert stt._provider.api_key == "env-key"
