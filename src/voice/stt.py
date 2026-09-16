"""Speech-to-text provider factory."""

import logging
import os
from typing import BinaryIO, Literal, cast

logger = logging.getLogger(__name__)

Provider = Literal["alibaba"]


class SpeechToText:
    """Load and delegate to the configured speech-to-text provider."""

    def __init__(
        self,
        provider: Provider = "alibaba",
        api_key: str | None = None,
        **config,
    ) -> None:
        self._provider_name = provider
        resolved_api_key = api_key or self._get_api_key(provider)
        self._provider = self._load_provider(provider, resolved_api_key, config)
        logger.info("SpeechToText created with provider=%s", provider)

    @staticmethod
    def _get_api_key(provider: Provider) -> str | None:
        if provider == "alibaba":
            return os.getenv("DASHSCOPE_API_KEY")
        return None

    @staticmethod
    def _load_provider(provider: Provider, api_key: str | None, config: dict):
        if provider == "alibaba":
            from voice.providers.alibaba_stt import AlibabaSTT

            return AlibabaSTT(api_key=api_key, **config)
        raise ValueError(f"Unknown STT provider: {provider}. Available providers: alibaba")

    @property
    def provider(self) -> str:
        return self._provider_name

    @classmethod
    def from_env(cls) -> "SpeechToText | None":
        """Create Bailian STT from environment configuration.

        If ``VOICE_STT_PROVIDER`` is unset, a configured ``DASHSCOPE_API_KEY``
        automatically enables Alibaba Cloud Bailian. Set the provider variable
        to an empty value to disable speech input explicitly.
        """
        configured_provider = os.getenv("VOICE_STT_PROVIDER")
        if configured_provider is None:
            provider = "alibaba" if os.getenv("DASHSCOPE_API_KEY") else ""
        else:
            provider = configured_provider.strip().lower()

        if not provider:
            logger.debug("Bailian STT disabled")
            return None

        try:
            return cls(provider=cast(Provider, provider))
        except Exception as exc:
            logger.error("Failed to create STT provider: %s", exc, exc_info=True)
            return None

    def transcribe(self, audio_file: BinaryIO) -> str:
        return self._provider.transcribe(audio_file)
