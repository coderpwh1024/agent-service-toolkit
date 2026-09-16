"""Alibaba Cloud Bailian speech-to-text implementation."""

import base64
import logging
import os
from pathlib import Path
from typing import BinaryIO

import httpx

logger = logging.getLogger(__name__)


class AlibabaSTT:
    """Speech recognition using Qwen ASR on Alibaba Cloud Bailian."""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DEFAULT_MODEL = "qwen3-asr-flash"
    MAX_AUDIO_BYTES = 7 * 1024 * 1024
    REQUEST_TIMEOUT = 120.0

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        resolved_api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not resolved_api_key:
            raise ValueError("DASHSCOPE_API_KEY must be configured for Bailian STT")

        self.api_key = resolved_api_key
        self.base_url = (
            base_url or os.getenv("DASHSCOPE_BASE_URL") or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or os.getenv("VOICE_STT_MODEL") or self.DEFAULT_MODEL
        logger.info("Alibaba Bailian STT initialized: model=%s", self.model)

    @staticmethod
    def _get_mime_type(audio_file: BinaryIO) -> str:
        declared_type = getattr(audio_file, "type", None)
        if isinstance(declared_type, str) and declared_type.startswith("audio/"):
            return declared_type.split(";", maxsplit=1)[0]

        suffix = Path(str(getattr(audio_file, "name", ""))).suffix.lower()
        return {
            ".flac": "audio/flac",
            ".m4a": "audio/mp4",
            ".mp3": "audio/mpeg",
            ".ogg": "audio/ogg",
            ".webm": "audio/webm",
            ".wav": "audio/wav",
        }.get(suffix, "audio/wav")

    def transcribe(self, audio_file: BinaryIO) -> str:
        """Return the recognized text, or an empty string if recognition fails."""
        try:
            audio_file.seek(0)
            audio_bytes = audio_file.read()
            if not isinstance(audio_bytes, (bytes, bytearray)) or not audio_bytes:
                raise ValueError("The recorded audio is empty")
            if len(audio_bytes) > self.MAX_AUDIO_BYTES:
                raise ValueError(
                    f"Audio is too large ({len(audio_bytes)} bytes); "
                    f"maximum is {self.MAX_AUDIO_BYTES} bytes"
                )

            mime_type = self._get_mime_type(audio_file)
            encoded_audio = base64.b64encode(audio_bytes).decode("ascii")
            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_audio",
                                "input_audio": {"data": f"data:{mime_type};base64,{encoded_audio}"},
                            }
                        ],
                    }
                ],
                "stream": False,
                "asr_options": {"enable_itn": False},
            }
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Bailian ASR returned an unexpected response")

            transcribed = content.strip()
            logger.info("Alibaba Bailian STT transcribed %s chars", len(transcribed))
            return transcribed
        except Exception as exc:
            logger.error("Alibaba Bailian STT failed: %s", exc, exc_info=True)
            return ""
