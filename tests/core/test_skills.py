from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from core.skills import EMAIL_SKILL_NAME, VIDEO_SKILL_NAME, read_email_skill, read_skill


def archive(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as package:
        for name, content in files.items():
            package.writestr(name, content)
    return buffer.getvalue()


def test_repository_skill_is_valid_and_loads_from_root_or_named_directory():
    content = Path(f"skills/{EMAIL_SKILL_NAME}/SKILL.md").read_text()
    for path in ["SKILL.md", f"{EMAIL_SKILL_NAME}/SKILL.md"]:
        skill = read_email_skill(archive({path: content}))
        assert skill.name == EMAIL_SKILL_NAME
        assert "语音" in skill.description
        assert "POST /skills/email/send" in skill.instructions
        assert len(skill.digest) == 64


@pytest.mark.parametrize(
    "files",
    [
        {"../SKILL.md": "unsafe"},
        {"/SKILL.md": "unsafe"},
        {"SKILL.md": "missing metadata"},
        {"SKILL.md": "---\nname: wrong\ndescription: test\n---\ncontent"},
        {"SKILL.md": "---\nname: send-current-user-email\n---\ncontent"},
        {"SKILL.md": "x" * 32001},
        {"SKILL.md": "content", f"{EMAIL_SKILL_NAME}/SKILL.md": "content"},
    ],
)
def test_invalid_or_unsafe_skill_is_rejected(files):
    with pytest.raises(ValueError):
        read_email_skill(archive(files))


def test_invalid_zip_is_rejected():
    with pytest.raises(ValueError):
        read_email_skill(b"not a zip")


def test_video_skill_loads_and_rejects_wrong_capability():
    content = Path(f"skills/{VIDEO_SKILL_NAME}/SKILL.md").read_text()
    for path in ["SKILL.md", f"{VIDEO_SKILL_NAME}/SKILL.md"]:
        skill = read_skill(archive({path: content}), VIDEO_SKILL_NAME)
        assert skill.name == VIDEO_SKILL_NAME
        assert "watermark=false" in skill.instructions
        assert "编辑" in skill.description
    with pytest.raises(ValueError):
        read_email_skill(archive({"SKILL.md": content}))
    with pytest.raises(ValueError):
        read_skill(archive({"../SKILL.md": content}), VIDEO_SKILL_NAME)
