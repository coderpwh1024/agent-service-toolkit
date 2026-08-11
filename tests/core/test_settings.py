import os
from unittest.mock import patch

from pydantic import SecretStr

from core.settings import Settings
from schema.models import QwenModelName


def test_settings_with_dashscope_key_enables_qwen_models():
    with patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test_key"}, clear=True):
        settings = Settings(_env_file=None)

    assert settings.DASHSCOPE_API_KEY == SecretStr("test_key")
    assert settings.DEFAULT_MODEL == QwenModelName.QWEN_37_FLASH
    assert settings.AVAILABLE_MODELS == set(QwenModelName)


def test_settings_accepts_explicit_qwen_default_model():
    with patch.dict(
        os.environ,
        {
            "DASHSCOPE_API_KEY": "test_key",
            "DEFAULT_MODEL": "qwen3.8-max",
        },
        clear=True,
    ):
        settings = Settings(_env_file=None)

    assert settings.DEFAULT_MODEL == QwenModelName.QWEN_38_MAX
