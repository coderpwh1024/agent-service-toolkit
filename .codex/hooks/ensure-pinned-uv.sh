#!/usr/bin/env bash
set -uo pipefail

[ "${CODEX_ENSURE_PINNED_UV:-}" = "true" ] || exit 0

payload="$(cat)"
cwd="$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("cwd", "."))' <<<"$payload" 2>/dev/null)" || exit 0
project_dir="$(git -C "$cwd" rev-parse --show-toplevel 2>/dev/null)" || exit 0
pinned="$(grep -hoE 'uv==[0-9]+\.[0-9]+\.[0-9]+' "$project_dir"/docker/Dockerfile.* 2>/dev/null | head -1 | cut -d= -f3)"
[ -n "$pinned" ] || exit 0

current="$(uv --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
[ "$current" = "$pinned" ] && exit 0

python3 -m pip install --user --quiet --no-cache-dir "uv==$pinned" >/dev/null 2>&1 || true
exit 0
