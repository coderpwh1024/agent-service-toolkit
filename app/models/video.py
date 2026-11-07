"""Video storage records and shared display metadata rules."""

import re
from datetime import datetime

from app.schemas.video import VideoCreate, VideoEdit, VideoTask


class VideoRecord(VideoTask):
    qiniu_cover_object_key: str | None = None
    idempotency_key: str
    request_hash: str
    request_payload: VideoCreate | VideoEdit
    provider_request_id: str | None
    create_by: int
    update_by: int
    is_delete: int
    next_poll_time: datetime


def derive_video_title(prompt: str) -> str:
    sentences = re.split(r"[\n。！？!?；;]|\.(?:\s+|$)", prompt.strip())
    first = next((sentence for sentence in sentences if sentence.strip()), prompt)
    return " ".join(first.split())[:80]
