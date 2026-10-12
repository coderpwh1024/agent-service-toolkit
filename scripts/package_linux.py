"""Build a Linux source deployment archive without local credentials or runtime files."""

import argparse
import hashlib
import io
import subprocess
import tarfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path

PACKAGE_ROOT = "agent-service-toolkit"
PACKAGE_FILES = (
    "pyproject.toml",
    "uv.lock",
    "README.md",
    "LICENSE",
    "scripts/package_linux.py",
    "deploy/uv-version",
    "deploy/.env.example",
    "deploy/install.sh",
    "deploy/run.py",
    "deploy/nginx/agent-service-toolkit.conf",
    "deploy/systemd/agent-service.service",
    "deploy/systemd/agent-streamlit.service",
    "docs/Linux_NoDocker_Deployment.md",
)


def export_requirements(root: Path, uv: str) -> str:
    expected = (root / "deploy" / "uv-version").read_text(encoding="utf-8").strip()
    version = subprocess.check_output([uv, "--version"], text=True).split()[1]
    if version != expected:
        raise ValueError(f"Packaging requires uv {expected}; installed: {version}")
    return subprocess.check_output(
        [uv, "export", "--frozen", "--no-dev", "--no-emit-project", "--no-header"],
        cwd=root,
        text=True,
    )


def build_archive(root: Path, destination: Path, requirements: str) -> None:
    paths = [root / name for name in PACKAGE_FILES]
    paths.extend(
        path
        for path in sorted((root / "src").rglob("*"))
        if path.suffix in {".py", ".sql"}
        and not any(
            part.startswith(".") or part == "__pycache__" for part in path.relative_to(root).parts
        )
    )
    for path in paths:
        relative = path.relative_to(root)
        if (
            not path.is_file()
            or path.is_symlink()
            or any((root / parent).is_symlink() for parent in relative.parents)
        ):
            raise ValueError(f"Package inputs must be regular files: {path.relative_to(root)}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(destination, "x:gz") as archive:
        for path in paths:
            relative = path.relative_to(root).as_posix()
            if relative == "deploy/.env.example":
                relative = ".env.example"
            info = archive.gettarinfo(str(path), f"{PACKAGE_ROOT}/{relative}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if path.suffix == ".sh" else 0o644
            with path.open("rb") as source:
                archive.addfile(info, source)
            if relative == ".env.example":
                info.name = f"{PACKAGE_ROOT}/deploy/.env.example"
                with path.open("rb") as source:
                    archive.addfile(info, source)

        contents = requirements.encode("utf-8")
        info = tarfile.TarInfo(f"{PACKAGE_ROOT}/requirements.txt")
        info.size = len(contents)
        info.mode = 0o644
        archive.addfile(info, io.BytesIO(contents))


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=root / "output")
    parser.add_argument("--uv", default="uv", help="Path to the repository's pinned uv executable")
    args = parser.parse_args()

    with (root / "pyproject.toml").open("rb") as source:
        version = tomllib.load(source)["project"]["version"]
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    destination = args.output_dir / f"{PACKAGE_ROOT}-{version}-linux-{timestamp}.tar.gz"
    build_archive(root, destination, export_requirements(root, args.uv))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum = destination.with_name(f"{destination.name}.sha256")
    checksum.write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    print(f"Package: {destination}")
    print(f"SHA256:  {checksum}")


if __name__ == "__main__":
    main()
