import pytest

from core.settings import Settings
from schema.voice import VoiceOption
from voice.catalog import speech_endpoint, speech_protocol, voice_options


@pytest.mark.parametrize(
    ("model", "role", "expected"),
    [
        ("qwen3-asr-flash-realtime-2026-02-10", "stt", "realtime"),
        ("qwen-audio-3.1-asr-flash-message", "stt", "inference"),
        ("qwen-audio-3.1-asr-flash-streaming", "stt", "inference"),
        ("fun-asr-realtime-2026-02-28", "stt", "inference"),
        ("paraformer-realtime-v2", "stt", "inference"),
        ("qwen3-tts-flash-realtime", "tts", "realtime"),
        ("qwen3-tts-vc-realtime-2026-01-15", "tts", "realtime"),
        ("qwen-audio-3.1-tts-flash", "tts", "inference"),
        ("qwen-audio-3.0-tts-plus", "tts", "inference"),
        ("cosyvoice-v3.5-flash", "tts", "inference"),
    ],
)
def test_model_families_select_their_streaming_protocol(model, role, expected):
    assert speech_protocol(model, role, "auto") == expected


@pytest.mark.parametrize("model", ["new-model", "qwen3-asr-flash-filetrans", "qwen3-asr-flash"])
def test_unknown_and_file_models_need_an_explicit_supported_protocol(model):
    with pytest.raises(ValueError, match="Unsupported STT"):
        speech_protocol(model, "stt", "auto")
    assert speech_protocol(model, "stt", "inference") == "inference"


@pytest.mark.parametrize(
    "host",
    [
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "workspace.cn-beijing.maas.aliyuncs.com",
    ],
)
def test_protocol_changes_preserve_the_configured_region_and_workspace(host):
    assert (
        speech_endpoint(f"wss://{host}/api-ws/v1/realtime", "inference")
        == f"wss://{host}/api-ws/v1/inference"
    )
    assert (
        speech_endpoint(f"wss://{host}/api-ws/v1/inference", "realtime")
        == f"wss://{host}/api-ws/v1/realtime"
    )


@pytest.mark.parametrize(
    "url",
    [
        "ws://example.com/realtime",
        "wss://secret@example.com/realtime",
        "wss://example.com/realtime?token=secret",
        "wss:///realtime",
    ],
)
def test_invalid_endpoints_are_rejected_without_echoing_credentials(url):
    with pytest.raises(ValueError) as caught:
        speech_endpoint(url, "inference")
    assert "secret" not in str(caught.value)


def test_upgrading_to_qwen_audio_does_not_reuse_the_old_voice_ids():
    config = Settings(
        _env_file=None,
        USE_FAKE_MODEL=True,
        VOICE_REALTIME_TTS_MODEL="qwen-audio-3.1-tts-flash",
        VOICE_REALTIME_VOICES=["Cherry", "Serena"],
    )
    assert voice_options(config)[0].id == "longanlingxin_v3.1"
    assert not {"Cherry", "Serena"} & {v.id for v in voice_options(config)}


def test_new_models_can_supply_protocol_endpoint_and_a_bound_voice_catalog():
    config = Settings(
        _env_file=None,
        USE_FAKE_MODEL=True,
        VOICE_ENABLED=True,
        VOICE_REALTIME_TTS_MODEL="future-tts",
        VOICE_REALTIME_TTS_PROTOCOL="inference",
        VOICE_REALTIME_TTS_URL="wss://speech.example.com/custom",
        VOICE_REALTIME_VOICE_CATALOG={"future-tts": [{"id": "custom-voice", "name": "自定义音色"}]},
    )
    config.require_voice_configuration()
    assert [v.id for v in voice_options(config)] == ["custom-voice"]
    config.VOICE_REALTIME_TTS_MODEL = "another-future-tts"
    with pytest.raises(ValueError, match="voice_catalog"):
        config.require_voice_configuration()


def test_empty_or_duplicate_model_catalogs_fail_before_startup():
    for voices in [[], ["voice", "voice"]]:
        config = Settings(
            _env_file=None,
            USE_FAKE_MODEL=True,
            VOICE_ENABLED=True,
            VOICE_REALTIME_VOICE_CATALOG={"qwen3-tts-flash-realtime": voices},
        )
        with pytest.raises(ValueError, match="nonempty catalog with unique"):
            config.require_voice_configuration()


def test_voice_cloning_models_need_a_bound_catalog_instead_of_system_voices():
    config = Settings(
        _env_file=None,
        USE_FAKE_MODEL=True,
        VOICE_ENABLED=True,
        VOICE_REALTIME_TTS_MODEL="qwen3-tts-vc-realtime-2026-01-15",
    )
    with pytest.raises(ValueError, match="voice_catalog"):
        config.require_voice_configuration()
    config.VOICE_REALTIME_VOICE_CATALOG = {
        config.VOICE_REALTIME_TTS_MODEL: [VoiceOption(id="enrolled-voice", name="复刻音色")]
    }
    config.require_voice_configuration()
