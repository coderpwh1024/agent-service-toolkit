"""Read Nacos skill instructions without executing downloaded code."""

import hashlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile

import yaml

EMAIL_SKILL_NAME = "send-current-user-email"


@dataclass(frozen=True)
class EmailSkill:
    name: str
    description: str
    instructions: str
    digest: str


def read_email_skill(archive: bytes) -> EmailSkill:
    if len(archive) > 1024 * 1024:
        raise ValueError("Email skill archive is too large")
    try:
        with ZipFile(BytesIO(archive)) as package:
            entries = package.infolist()
            if len(entries) > 30 or sum(entry.file_size for entry in entries) > 1024 * 1024:
                raise ValueError("Email skill archive exceeds resource limits")
            for entry in entries:
                path = PurePosixPath(entry.filename)
                if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                    raise ValueError("Email skill archive contains an unsafe path")
            candidates = [
                entry
                for entry in entries
                if entry.filename in {"SKILL.md", f"{EMAIL_SKILL_NAME}/SKILL.md"}
            ]
            if len(candidates) != 1 or candidates[0].file_size > 32000:
                raise ValueError("Email skill must contain one bounded SKILL.md")
            content = package.read(candidates[0]).decode("utf-8")
    except (BadZipFile, UnicodeDecodeError) as exc:
        raise ValueError("Invalid email skill archive") from exc
    parts = content.split("---", 2)
    if len(parts) != 3 or parts[0].strip():
        raise ValueError("Email SKILL.md requires YAML frontmatter")
    metadata = yaml.safe_load(parts[1])
    if not isinstance(metadata, dict) or metadata.get("name") != EMAIL_SKILL_NAME:
        raise ValueError("Email skill name does not match the runtime capability")
    description = metadata.get("description")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
        raise ValueError("Email skill requires a description of at most 1024 characters")
    if not parts[2].strip():
        raise ValueError("Email skill instructions cannot be empty")
    return EmailSkill(
        name=EMAIL_SKILL_NAME,
        description=description.strip(),
        instructions=parts[2].strip(),
        digest=hashlib.sha256(content.encode()).hexdigest(),
    )
