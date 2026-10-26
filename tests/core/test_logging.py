import json
import logging

from core.logging import JsonFormatter, ServiceLogFilter


def test_json_logs_have_timestamp_and_redact_exception_value():
    try:
        raise ValueError("secret-user-content")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "memory.mem0_service",
            logging.ERROR,
            __file__,
            1,
            "operation failed",
            (),
            sys.exc_info(),
        )
    payload = json.loads(JsonFormatter().format(record))
    assert payload["error_type"] == "ValueError"
    assert payload["message"] == "operation failed"
    assert payload["timestamp"]
    assert "secret-user-content" not in json.dumps(payload)


def test_sdk_payload_logs_are_filtered():
    log_filter = ServiceLogFilter()
    for name in ["mem0.memory.main", "httpx", "openai._base_client", "ddgs.ddgs", "primp"]:
        assert not log_filter.filter(logging.LogRecord(name, logging.DEBUG, "", 1, "", (), None))
    assert log_filter.filter(
        logging.LogRecord("memory.mem0_service", logging.INFO, "", 1, "", (), None)
    )
