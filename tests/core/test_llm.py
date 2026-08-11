from unittest.mock import patch

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from core.llm import get_model
from schema.models import QwenModelName


def test_get_model_qwen_uses_dashscope_openai_compatible_endpoint():
    base_url = "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    with (
        patch("core.llm.settings.DASHSCOPE_API_KEY", SecretStr("test_key")),
        patch("core.llm.settings.DASHSCOPE_BASE_URL", base_url),
    ):
        get_model.cache_clear()
        model = get_model(QwenModelName.QWEN_37_PLUS)

    get_model.cache_clear()
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "qwen3.7-plus"
    assert str(model.openai_api_base).rstrip("/") == base_url
    assert model.streaming is True
    assert model.temperature == 0.5
