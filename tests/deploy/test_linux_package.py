import os
import runpy
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGER = runpy.run_path(str(ROOT / "scripts" / "package_linux.py"))


@pytest.fixture
def package_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for name in PACKAGER["PACKAGE_FILES"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("template\n", encoding="utf-8")
    for name in (
        "src/run_service.py",
        "src/service/sql/create_app_users.sql",
        "src/__pycache__/old.pyc",
        "src/.hidden/private.py",
        ".env",
        ".env.local",
        "privatecredentials/token.pem",
        "data/customer.pdf",
        "tmp/service.log",
        ".venv/bin/python",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    return root


def test_package_includes_runtime_sql_and_excludes_local_data(
    package_root: Path, tmp_path: Path
) -> None:
    destination = tmp_path / "package.tar.gz"
    PACKAGER["build_archive"](package_root, destination, "example==1.0\n")
    with tarfile.open(destination) as archive:
        names = set(archive.getnames())
        assert "agent-service-toolkit/src/run_service.py" in names
        assert "agent-service-toolkit/src/service/sql/create_app_users.sql" in names
        assert "agent-service-toolkit/.env.example" in names
        assert "agent-service-toolkit/deploy/.env.example" in names
        assert "agent-service-toolkit/deploy/nginx/agent-service-toolkit.conf" in names
        assert not any(
            name.endswith(("/.env", "/.env.local", ".pyc", "private.py"))
            or any(part in name for part in ("privatecredentials/", "data/", "tmp/", ".venv/"))
            for name in names
        )
        requirements = archive.extractfile("agent-service-toolkit/requirements.txt")
        assert requirements is not None
        assert requirements.read() == b"example==1.0\n"
        assert all(member.isreg() and member.uid == 0 for member in archive.getmembers())
        assert archive.getmember("agent-service-toolkit/deploy/install.sh").mode == 0o755


def test_package_rejects_source_symlink(package_root: Path, tmp_path: Path) -> None:
    (package_root / "src" / "credentials.py").symlink_to(package_root / ".env")
    with pytest.raises(ValueError, match="regular files"):
        PACKAGER["build_archive"](package_root, tmp_path / "package.tar.gz", "")
    assert not (tmp_path / "package.tar.gz").exists()


@pytest.mark.parametrize("component", ["service", "app"])
def test_launcher_loads_deployment_configuration_before_exec(
    component: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copyfile(ROOT / "deploy" / "run.py", deploy / "run.py")
    (tmp_path / ".env").write_text(
        "MODE=prod\nNACOS_ENABLED=false\nPORT=8000\nAUTH_SECRET=test-service-secret\n",
        encoding="utf-8",
    )
    for name in ("MODE", "NACOS_ENABLED", "AUTH_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setattr(sys, "argv", ["run.py", component])
    monkeypatch.chdir(tmp_path.parent)
    calls: list[tuple[str, list[str]]] = []

    def capture_exec(executable: str, command: list[str]) -> None:
        calls.append((executable, command))

    monkeypatch.setattr(os, "execv", capture_exec)
    launcher = runpy.run_path(str(deploy / "run.py"))
    launcher["main"]()

    assert Path.cwd() == tmp_path
    assert os.environ["MODE"] == "prod"
    assert os.environ["NACOS_ENABLED"] == "false"
    assert os.environ["AUTH_SECRET"] == "test-service-secret"
    assert os.environ["PORT"] == "9000"
    assert calls[0][0] == sys.executable
    if component == "service":
        assert calls[0][1][1] == str(tmp_path / "src" / "run_service.py")
    else:
        assert calls[0][1][1:4] == ["-m", "streamlit", "run"]
        assert calls[0][1][4] == str(tmp_path / "src" / "streamlit_app.py")


def test_launcher_requires_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    shutil.copyfile(ROOT / "deploy" / "run.py", deploy / "run.py")
    monkeypatch.setattr(sys, "argv", ["run.py", "service"])
    launcher = runpy.run_path(str(deploy / "run.py"))
    with pytest.raises(SystemExit) as error:
        launcher["main"]()
    assert error.value.code == 2


@pytest.mark.parametrize("existing_config", [False, True])
@pytest.mark.parametrize("glibc_version", ["2.28", "2.34", "2.39"])
def test_installer_preserves_config_and_uses_local_python(
    package_root: Path, existing_config: bool, glibc_version: str
) -> None:
    shutil.copyfile(ROOT / "deploy" / "install.sh", package_root / "deploy" / "install.sh")
    shutil.copyfile(ROOT / "deploy" / "uv-version", package_root / "deploy" / "uv-version")
    if not existing_config:
        (package_root / ".env").unlink()
    commands = package_root / "test-bin"
    commands.mkdir()
    (commands / "uname").write_text("#!/bin/sh\necho Linux\n", encoding="utf-8")
    (commands / "getconf").write_text(
        f"#!/bin/sh\necho 'glibc {glibc_version}'\n", encoding="utf-8"
    )
    (commands / "uv").write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then\n'
        '  echo "uv 0.11.32"\nelse\n'
        '  printf "%s\\n" "$*" "$UV_PROJECT_ENVIRONMENT" "$UV_PYTHON_INSTALL_DIR" '
        "> install-args.txt\nfi\n",
        encoding="utf-8",
    )
    for path in commands.iterdir():
        path.chmod(0o755)
    subprocess.run(
        ["/bin/bash", str(package_root / "deploy" / "install.sh")],
        cwd=package_root.parent,
        env={"PATH": f"{commands}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    config = package_root / ".env"
    assert config.read_text(encoding="utf-8") == ("fixture\n" if existing_config else "template\n")
    if not existing_config:
        assert config.stat().st_mode & 0o777 == 0o600
    install_args = (package_root / "install-args.txt").read_text(encoding="utf-8").splitlines()
    assert install_args == [
        "sync --frozen --no-dev --no-install-project --managed-python --python 3.13.14",
        str(package_root / ".venv"),
        str(package_root / ".python"),
    ]


@pytest.mark.parametrize("libc_info", ["glibc 2.17", "glibc 2.27", "musl 1.2.5", "unknown"])
def test_installer_rejects_incompatible_libc_before_install(
    package_root: Path, libc_info: str
) -> None:
    shutil.copyfile(ROOT / "deploy" / "install.sh", package_root / "deploy" / "install.sh")
    commands = package_root / "test-bin"
    commands.mkdir()
    (commands / "uname").write_text("#!/bin/sh\necho Linux\n", encoding="utf-8")
    (commands / "getconf").write_text(f"#!/bin/sh\necho '{libc_info}'\n", encoding="utf-8")
    (commands / "uv").write_text("#!/bin/sh\ntouch unexpected-install\n", encoding="utf-8")
    for path in commands.iterdir():
        path.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(package_root / "deploy" / "install.sh")],
        cwd=package_root.parent,
        env={"PATH": f"{commands}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "glibc >= 2.28" in result.stderr
    assert not (package_root / "unexpected-install").exists()
    assert (package_root / ".env").read_text(encoding="utf-8") == "fixture\n"
