import logging
import os
from dataclasses import dataclass
from enum import StrEnum


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    def to_logging_level(self) -> int:
        return logging.getLevelNamesMapping()[self]


def load_log_level() -> LogLevel:
    value = os.getenv("LOG_LEVEL", LogLevel.WARNING).upper()
    try:
        return LogLevel(value)
    except ValueError as error:
        choices = ", ".join(LogLevel)
        raise ValueError(f"LOG_LEVEL 必须是以下值之一：{choices}") from error


@dataclass(frozen=True, slots=True)
class Settings:
    LOG_LEVEL: LogLevel


settings = Settings(LOG_LEVEL=load_log_level())
