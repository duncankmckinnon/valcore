#!/bin/bash
set -euo pipefail
home="${EVALCORE_HOME:-$HOME/.eval-core}"
venv="$home/venv"
stamp="$venv/.version"
version="${EVALCORE_VERSION:?EVALCORE_VERSION not set}"

if [ ! -x "$venv/bin/eval-core" ] || [ "$(cat "$stamp" 2>/dev/null)" != "$version" ]; then
  mkdir -p "$home" && chmod 700 "$home"
  echo "eval-core: provisioning environment (first run)…" >&2
  uv venv --python 3.12 "$venv" >&2
  uv pip install --python "$venv/bin/python" "eval-core==$version" >&2
  printf '%s' "$version" > "$stamp"
fi

exec "$venv/bin/eval-core" "$@"
