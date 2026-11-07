"""Start the real FastAPI service with isolated fake-model smoke configuration."""

from app.__main__ import main as run_service
from app.core import settings
from app.schemas.llm import FakeModelName
from app.schemas.prompts import Mem0Prompts


def main() -> None:
    if not settings.USE_FAKE_MODEL:
        raise ValueError("The smoke server requires USE_FAKE_MODEL=true")
    settings.MODE = "prod"
    settings.DEFAULT_MODEL = FakeModelName.FAKE
    settings.AVAILABLE_MODELS = {FakeModelName.FAKE}
    settings.NACOS_ENABLED = False
    settings.AUTH_SECRET = None
    settings.APP_TOKEN_SECRET = None
    settings.MEM0_ENABLED = False
    settings.EMAIL_AUTH_ENABLED = False
    settings.EMAIL_SKILL_ENABLED = False
    settings.VIDEO_ENABLED = False
    settings.VIDEO_SKILL_ENABLED = False
    settings.VOICE_ENABLED = False
    settings.FILE_UPLOAD_ENABLED = False
    settings.mem0_prompts = Mem0Prompts(
        chat_system="You are a deterministic smoke-test chatbot. Do not call external tools.",
        memory_status_template="Memory status: {status}",
        intent="Return MemoryIntent with action none.",
        match="Return MemoryMatch with no candidates.",
        extraction="Return an empty memory array.",
        extraction_system="Extract no facts in this isolated smoke test.",
    )
    run_service()


if __name__ == "__main__":
    main()
