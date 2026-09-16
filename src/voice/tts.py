"""Text-to-speech provider factory."""

import logging
import os
from typing import Literal, cast

logger = logging.getLogger(__name__)

Provider = Literal["alibaba"]


class TextToSpeech:
    """Load and delegate to the configured text-to-speech provider."""

    def __init__(
        self,
        provider: Provider = "alibaba",
        api_key: str | None = None,
        **config,
    ) -> None:
        self._provider_name = provider
        resolved_api_key = api_key or self._get_api_key(provider)
        self._provider = self._load_provider(provider, resolved_api_key, config)
        logger.info("TextToSpeech created with provider=%s", provider)

    @staticmethod
    def _get_api_key(provider: Provider) -> str | None:
        if provider == "alibaba":
            return os.getenv("DASHSCOPE_API_KEY")
        return None

    @staticmethod
    def _load_provider(provider: Provider, api_key: str | None, config: dict):
        if provider == "alibaba":
            from voice.providers.alibaba_tts import AlibabaTTS

            return AlibabaTTS(api_key=api_key, **config)
        raise ValueError(f"Unknown TTS provider: {provider}. Available providers: alibaba")

    @property
    def provider(self) -> str:
        return self._provider_name

    @classmethod
    def from_env(cls) -> "TextToSpeech | None":
        """Create Bailian TTS from environment configuration.

        If ``VOICE_TTS_PROVIDER`` is unset, a configured ``DASHSCOPE_API_KEY``
        automatically enables Alibaba Cloud Bailian. Set the provider variable
        to an empty value to disable speech output explicitly.
        """
        configured_provider = os.getenv("VOICE_TTS_PROVIDER")
        if configured_provider is None:
            provider = "alibaba" if os.getenv("DASHSCOPE_API_KEY") else ""
        else:
            provider = configured_provider.strip().lower()

        if not provider:
            logger.debug("Bailian TTS disabled")
            return None

        try:
            return cls(provider=cast(Provider, provider))
        except Exception as exc:
            logger.error("Failed to create TTS provider: %s", exc, exc_info=True)
            return None

    def generate(self, text: str) -> bytes | None:
        return self._provider.generate(text)

    def get_format(self) -> str:
        return self._provider.get_format()
