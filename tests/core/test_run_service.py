import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest

from core import logging as service_logging
from core import settings


@pytest.mark.parametrize("module_name,worker", [("__mp_main__", True), ("imported_entry", False)])
def test_reload_worker_configures_logs_without_starting_another_server(
    monkeypatch, module_name, worker
):
    import uvicorn

    configure = Mock()
    run = Mock()
    monkeypatch.setattr(service_logging, "configure_logging", configure)
    monkeypatch.setattr(uvicorn, "run", run)
    runpy.run_path(str(Path(__file__).parents[2] / "src/run_service.py"), run_name=module_name)
    run.assert_not_called()
    if worker:
        configure.assert_called_once_with(settings.LOG_LEVEL, settings.LOG_FORMAT)
    else:
        configure.assert_not_called()
