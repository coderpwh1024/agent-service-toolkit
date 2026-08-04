import logging
import os
import subprocess
import sys

import pytest

from core.settings import LogLevel


@pytest.mark.parametrize(
    ("log_level", "expected"),
    [
        (LogLevel.DEBUG, logging.DEBUG),
        (LogLevel.INFO, logging.INFO),
        (LogLevel.WARNING, logging.WARNING),
        (LogLevel.ERROR, logging.ERROR),
        (LogLevel.CRITICAL, logging.CRITICAL),
    ],
)
def test_log_level_conversion(log_level: LogLevel, expected: int) -> None:
    assert log_level.to_logging_level() == expected


def test_run_service_imports_settings() -> None:
    environment = os.environ | {"LOG_LEVEL": "DEBUG"}
    result = subprocess.run(
        [sys.executable, "src/run_service.py"],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, result.stderr
