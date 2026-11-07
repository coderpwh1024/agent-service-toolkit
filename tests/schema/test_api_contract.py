import json
from pathlib import Path

from app.main import app


def test_refactored_app_preserves_the_public_openapi_contract() -> None:
    baseline = json.loads(
        (Path(__file__).parents[1] / "contracts" / "openapi.json").read_text(encoding="utf-8")
    )
    assert app.openapi() == baseline


def test_refactored_app_preserves_the_websocket_client_protocol() -> None:
    from app.schemas.voice import client_event_adapter

    baseline = json.loads(
        (Path(__file__).parents[1] / "contracts" / "voice_protocol.json").read_text(
            encoding="utf-8"
        )
    )
    assert client_event_adapter.json_schema() == baseline
