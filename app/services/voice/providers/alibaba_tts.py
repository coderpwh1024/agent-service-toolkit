"""Alibaba Cloud Bailian text-to-speech implementation."""

import base64
import logging
import os

import httpx

logger = logging.getLogger(__name__)


class AlibabaTTS:
    """Speech synthesis using Qwen TTS on Alibaba Cloud Bailian."""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
    DEFAULT_MODEL = "qwen3-tts-flash"
    DEFAULT_VOICE = "Cherry"
    DEFAULT_LANGUAGE = "Auto"
    API_PATH = "/services/aigc/multimodal-generation/generation"
    MAX_TEXT_LENGTH = 600
    MIN_TEXT_LENGTH = 3
    REQUEST_TIMEOUT = 120.0

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        voice: str | None = None,
        model: str | None = None,
        language_type: str | None = None,
    ) -> None:
        resolved_api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not resolved_api_key:
            raise ValueError("DASHSCOPE_API_KEY must be configured for Bailian TTS")

        self.api_key = resolved_api_key
        self.base_url = (
            base_url or os.getenv("DASHSCOPE_NATIVE_BASE_URL") or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.voice = voice or os.getenv("VOICE_TTS_VOICE") or self.DEFAULT_VOICE
        self.model = model or os.getenv("VOICE_TTS_MODEL") or self.DEFAULT_MODEL
        self.language_type = (
            language_type or os.getenv("VOICE_TTS_LANGUAGE") or self.DEFAULT_LANGUAGE
        )
        logger.info(
            "Alibaba Bailian TTS initialized: voice=%s, model=%s",
            self.voice,
            self.model,
        )

    def _validate_and_prepare_text(self, text: str) -> str | None:
        text = text.strip()
        if len(text) < self.MIN_TEXT_LENGTH:
            logger.debug("Alibaba Bailian TTS skipped short text (%s chars)", len(text))
            return None
        if len(text) > self.MAX_TEXT_LENGTH:
            logger.warning(
                "Alibaba Bailian TTS truncating text from %s to %s chars",
                len(text),
                self.MAX_TEXT_LENGTH,
            )
            text = text[: self.MAX_TEXT_LENGTH]
        return text

    @staticmethod
    def _decode_audio_data(data: str) -> bytes:
        encoded = data.split(",", maxsplit=1)[-1]
        return base64.b64decode(encoded, validate=True)

    def generate(self, text: str) -> bytes | None:
        """Return WAV audio bytes, or ``None`` if synthesis fails."""
        prepared_text = self._validate_and_prepare_text(text)
        if not prepared_text:
            return None

        try:
            response = httpx.post(
                f"{self.base_url}{self.API_PATH}",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": {
                        "text": prepared_text,
                        "voice": self.voice,
                        "language_type": self.language_type,
                    },
                },
                timeout=self.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            audio = response.json()["output"]["audio"]

            if data := audio.get("data"):
                audio_bytes = self._decode_audio_data(data)
            elif url := audio.get("url"):
                download = httpx.get(
                    url,
                    timeout=self.REQUEST_TIMEOUT,
                    follow_redirects=True,
                )
                download.raise_for_status()
                audio_bytes = download.content
            else:
                raise ValueError("Bailian TTS response did not include audio data")

            if not audio_bytes:
                raise ValueError("Bailian TTS returned empty audio")
            logger.info("Alibaba Bailian TTS generated %s bytes", len(audio_bytes))
            return audio_bytes
        except Exception as exc:
            logger.error("Alibaba Bailian TTS failed: %s", exc, exc_info=True)
            return None

    def get_format(self) -> str:
        """Return the MIME type produced by Qwen TTS."""
        return "audio/wav"
