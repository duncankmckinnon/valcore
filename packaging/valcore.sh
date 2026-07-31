#!/bin/bash
set -euo pipefail
home="${VALCORE_HOME:-$HOME/.valcore}"
venv="$home/venv"
stamp="$venv/.version"
version="${VALCORE_VERSION:?VALCORE_VERSION not set}"

if [ ! -x "$venv/bin/valcore" ] || [ "$(cat "$stamp" 2>/dev/null)" != "$version" ]; then
  mkdir -p "$home" && chmod 700 "$home"
  echo "valcore: provisioning environment (first run)…" >&2
  uv venv --python 3.12 "$venv" >&2
  uv pip install --python "$venv/bin/python" "valcore==$version" >&2
  printf '%s' "$version" > "$stamp"
fi

exec "$venv/bin/valcore" "$@"
