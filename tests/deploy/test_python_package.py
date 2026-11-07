import shutil
import subprocess
import sys
from email.parser import BytesParser
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]


def test_python_build_includes_both_packages_and_database_schemas(tmp_path: Path) -> None:
    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE"):
        shutil.copyfile(ROOT / name, tmp_path / name)
    for name in ("app", "client"):
        shutil.copytree(
            ROOT / name,
            tmp_path / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "from setuptools import build_meta; build_meta.build_wheel('dist'); "
            "build_meta.build_editable('editable-dist')",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(list((tmp_path / "editable-dist").glob("*.whl"))) == 1
    wheels = list((tmp_path / "dist").glob("*.whl"))
    assert len(wheels) == 1
    assert wheels[0].name.startswith("agent_voice_server-")
    with ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
        metadata_path = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(wheel.read(metadata_path))
        assert metadata["License-Expression"] == "Apache-2.0"
        assert set(metadata.get_all("License-File", [])) == {"LICENSE", "NOTICE"}
        distribution = metadata_path.removesuffix("/METADATA")
        for name in ("LICENSE", "NOTICE"):
            assert wheel.read(f"{distribution}/licenses/{name}") == (ROOT / name).read_bytes()
        assert {"app/main.py", "app/agents/mem0_chat.py", "client/client.py"} <= names
        for schema in (ROOT / "app" / "models" / "sql").glob("*.sql"):
            path = schema.relative_to(ROOT).as_posix()
            assert wheel.read(path) == schema.read_bytes()
        assert not any(name.startswith(("tests/", "src/")) for name in names)
