"""Public metadata for confirmed Qiniu file uploads."""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class UploadedFile(BaseModel):
    id: int = Field(gt=0)
    url: HttpUrl
    original_name: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(gt=0)
    uploaded_at: datetime
