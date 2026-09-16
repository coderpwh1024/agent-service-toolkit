"""Tests for the text-to-speech factory."""

import os
from unittest.mock import patch

import pytest

from voice.tts import TextToSpeech


def test_init_with_alibaba_provider():
    tts = TextToSpeech(provider="alibaba", api_key="test-key")
    assert tts.provider == "alibaba"


def test_init_with_invalid_provider():
    with pytest.raises(ValueError, match="Unknown TTS provider: invalid"):
        TextToSpeech(provider="invalid", api_key="test-key")  # type: ignore[arg-type]


def test_from_env_provider_and_key_not_set():
    with patch.dict(os.environ, {}, clear=True):
        assert TextToSpeech.from_env() is None


def test_openai_key_does_not_enable_audio_replies():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "legacy-key"}, clear=True):
        assert TextToSpeech.from_env() is None


def test_from_env_auto_enables_alibaba_with_dashscope_key():
    with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test-key"}, clear=True):
        tts = TextToSpeech.from_env()
    assert tts is not None
    assert tts.provider == "alibaba"


def test_from_env_valid_explicit_provider():
    with patch.dict(
        os.environ,
        {"VOICE_TTS_PROVIDER": "alibaba", "DASHSCOPE_API_KEY": "test-key"},
        clear=True,
    ):
        tts = TextToSpeech.from_env()
    assert tts is not None
    assert tts.provider == "alibaba"


def test_from_env_empty_provider_disables_tts():
    with patch.dict(
        os.environ,
        {"VOICE_TTS_PROVIDER": "", "DASHSCOPE_API_KEY": "test-key"},
        clear=True,
    ):
        assert TextToSpeech.from_env() is None


def test_from_env_invalid_provider_returns_none():
    with patch.dict(
        os.environ,
        {"VOICE_TTS_PROVIDER": "invalid", "DASHSCOPE_API_KEY": "test-key"},
        clear=True,
    ):
        assert TextToSpeech.from_env() is None


def test_api_key_parameter_takes_precedence():
    with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key"}, clear=True):
        tts = TextToSpeech(provider="alibaba", api_key="param-key")
    assert tts._provider.api_key == "param-key"


def test_api_key_is_loaded_from_dashscope_environment():
    with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "env-key"}, clear=True):
        tts = TextToSpeech(provider="alibaba")
    assert tts._provider.api_key == "env-key"
