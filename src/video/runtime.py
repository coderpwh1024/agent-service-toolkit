"""Share the lifespan-managed video service with authenticated agent commands."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from video.service import VideoService

_service: "VideoService | None" = None


def get_video_service() -> "VideoService | None":
    return _service


def set_video_service(service: "VideoService | None") -> None:
    global _service
    _service = service
