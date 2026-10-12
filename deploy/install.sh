#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ "$(uname -s)" != Linux ]]; then
    echo 'Run this installer on the target Linux server.' >&2
    exit 1
fi

if ! libc_info="$(getconf GNU_LIBC_VERSION 2>/dev/null)"; then
    echo 'This package requires Linux with glibc >= 2.28.' >&2
    exit 1
fi
read -r libc_name libc_version <<< "$libc_info"
if [[ "$libc_name" != glibc || ! "$libc_version" =~ ^([0-9]+)\.([0-9]+)(\.[0-9]+)?$ ]]; then
    echo 'Cannot determine the glibc version; this package requires glibc >= 2.28.' >&2
    exit 1
fi
glibc_major="${BASH_REMATCH[1]}"
glibc_minor="${BASH_REMATCH[2]}"
if (( glibc_major < 2 || (glibc_major == 2 && glibc_minor < 28) )); then
    echo "This package requires glibc >= 2.28; detected: ${libc_version}." >&2
    echo 'CentOS 7 is unsupported. Use CentOS Stream 9/10 or a compatible Enterprise Linux host.' >&2
    exit 1
fi

uv_version="$(cat deploy/uv-version)"
if ! command -v uv >/dev/null 2>&1; then
    echo "Install uv first: https://astral.sh/uv/${uv_version}/install.sh" >&2
    exit 1
fi
read -r _ installed_uv_version _ < <(uv --version)
if [[ "$installed_uv_version" != "$uv_version" ]]; then
    echo "This package requires uv ${uv_version}; installed: ${installed_uv_version}." >&2
    exit 1
fi

export UV_PROJECT_ENVIRONMENT="$project_root/.venv"
export UV_PYTHON_INSTALL_DIR="$project_root/.python"
export UV_LINK_MODE=copy
uv sync --frozen --no-dev --no-install-project --managed-python --python "${1:-3.13.14}"

if [[ ! -e .env ]]; then
    (umask 077; cp deploy/.env.example .env)
fi

echo 'Dependencies installed. Edit .env, then run:'
echo '  .venv/bin/python deploy/run.py service'
echo '  .venv/bin/python deploy/run.py app'
