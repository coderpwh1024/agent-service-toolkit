from typing import Protocol


class UploadContent(Protocol):
    filename: str | None
    content_type: str | None
    size: int | None

    async def read(self, size: int = -1) -> bytes: ...
