import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core.settings import Settings
from app.schemas.llm import FakeModelName


@pytest.mark.parametrize("fake", [False, True])
def test_smoke_server_requires_fake_mode_and_isolates_external_services(fake: bool) -> None:
    namespace = runpy.run_path(str(Path(__file__).parents[1] / "smoke" / "server.py"))
    main = namespace["main"]
    config = Settings(_env_file=None, USE_FAKE_MODEL=fake)
    server = Mock()
    main.__globals__["settings"] = config
    main.__globals__["run_service"] = server
    if not fake:
        with pytest.raises(ValueError, match="USE_FAKE_MODEL"):
            main()
        server.assert_not_called()
        return
    main()
    server.assert_called_once_with()
    assert config.MODE == "prod"
    assert config.DEFAULT_MODEL == FakeModelName.FAKE
    assert config.AVAILABLE_MODELS == {FakeModelName.FAKE}
    assert config.AUTH_SECRET is None and config.APP_TOKEN_SECRET is None
    assert all(
        not getattr(config, name)
        for name in (
            "NACOS_ENABLED",
            "MEM0_ENABLED",
            "EMAIL_AUTH_ENABLED",
            "EMAIL_SKILL_ENABLED",
            "VIDEO_ENABLED",
            "VIDEO_SKILL_ENABLED",
            "VOICE_ENABLED",
            "FILE_UPLOAD_ENABLED",
        )
    )
    assert config.mem0_prompts.chat_system
    assert config.mem0_prompts.memory_status_template.format(status="disabled")
