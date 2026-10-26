"""Resolve Bailian speech protocols and model-specific voice catalogs."""

import re
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit, urlunsplit

from schema.voice import VoiceOption

if TYPE_CHECKING:
    from core.settings import Settings

SpeechProtocol = Literal["auto", "realtime", "inference"]

_QWEN_AUDIO_31_VOICES = (
    ("longanlingxin_v3.1", "龙安灵心", "多语言与方言女声"),
    ("longanhuan_v3.1", "龙安欢", "多语言与方言女声"),
    ("longanfengyue_v3.1", "龙安风月", "多语言与方言女声"),
    ("xunanchuan_v3.1", "徐南川", "多语言与方言男声"),
    ("yuxiaoyun_v3.1", "余晓云", "阳光、亲切、自然，仅普通话"),
    ("xieshurou_v3.1", "谢舒柔", "自然、知性、温柔，仅普通话"),
)
_QWEN_AUDIO_30_VOICES = {
    "plus": (("longanlingxin", "龙安灵心", "温暖、共情"),),
    "flash": (
        ("longanfengyue", "龙安风月", "自然、亲切"),
        ("longanlingxi", "龙安灵汐", "可爱、甜美"),
    ),
}


def speech_protocol(model: str, role: Literal["stt", "tts"], override: SpeechProtocol) -> str:
    if not model.strip():
        raise ValueError(f"{role.upper()} model must not be blank")
    if override != "auto":
        return override
    if role == "stt":
        if re.match(r"^qwen(?:3)?-asr-flash-realtime(?:-|$)", model):
            return "realtime"
        if re.match(r"^qwen-audio-\d+\.\d+-asr-flash-(?:message|streaming)(?:-|$)", model):
            return "inference"
        if (
            re.match(r"^(?:fun-asr-realtime|paraformer-realtime)(?:-|$)", model)
            and "8k" not in model
        ):
            return "inference"
    else:
        if re.match(r"^qwen(?:3)?-tts-.*realtime(?:-|$)", model):
            return "realtime"
        if re.match(r"^qwen-audio-\d+\.\d+-tts-(?:flash|plus)(?:-|$)", model):
            return "inference"
        if model.startswith("cosyvoice-"):
            return "inference"
    raise ValueError(
        f"Unsupported {role.upper()} model family: {model}; configure its speech protocol "
        "explicitly only if it implements realtime or inference streaming events"
    )


def speech_endpoint(base: str, protocol: str, override: str | None = None) -> str:
    url = urlsplit(override or base)
    if url.scheme != "wss" or not url.hostname or url.query or url.fragment or url.username:
        raise ValueError("Speech endpoint must be a wss URL without credentials or a query")
    if override:
        return urlunsplit(url)
    if not url.path.endswith(("/realtime", "/inference")):
        raise ValueError("Speech endpoint needs a per-model URL for this path")
    return urlunsplit(url._replace(path=url.path.rsplit("/", 1)[0] + "/" + protocol))


def voice_options(config: "Settings") -> list[VoiceOption]:
    model = config.VOICE_REALTIME_TTS_MODEL
    catalog = config.VOICE_REALTIME_VOICE_CATALOG
    if model in catalog:
        return catalog[model]
    if config.VOICE_REALTIME_VOICES_MODEL == model:
        return config.VOICE_REALTIME_VOICES
    if re.match(r"^qwen-audio-3\.1-tts-flash(?:-|$)", model):
        defaults = _QWEN_AUDIO_31_VOICES
    elif match := re.match(r"^qwen-audio-3\.0-tts-(plus|flash)(?:-|$)", model):
        defaults = _QWEN_AUDIO_30_VOICES[match[1]]
    elif re.match(r"^(?:qwen3-tts-(?:instruct-)?flash-realtime|qwen-tts-realtime)(?:-|$)", model):
        if config.VOICE_REALTIME_VOICES_MODEL:
            raise ValueError("Configure a voice catalog for the selected TTS model")
        return config.VOICE_REALTIME_VOICES
    else:
        raise ValueError("Configure voice_catalog for the selected TTS model")
    return [
        VoiceOption(
            id=key,
            name=name,
            description=description,
            languages=["zh"] if key in {"yuxiaoyun_v3.1", "xieshurou_v3.1"} else ["zh", "en"],
        )
        for key, name, description in defaults
    ]


def validate_speech_configuration(config: "Settings") -> None:
    for role in ("stt", "tts"):
        model = getattr(config, f"VOICE_REALTIME_{role.upper()}_MODEL")
        protocol = speech_protocol(
            model, role, getattr(config, f"VOICE_REALTIME_{role.upper()}_PROTOCOL")
        )
        speech_endpoint(
            config.VOICE_REALTIME_URL,
            protocol,
            getattr(config, f"VOICE_REALTIME_{role.upper()}_URL"),
        )
    options = voice_options(config)
    if not options or len({voice.id for voice in options}) != len(options):
        raise ValueError("The selected TTS model needs a nonempty catalog with unique voice IDs")
