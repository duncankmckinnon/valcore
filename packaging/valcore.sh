#!/bin/bash
set -euo pipefail
home="${VALCORE_HOME:-$HOME/.valcore}"
venv="$home/venv"
stamp="$venv/.version"
version="${VALCORE_VERSION:?VALCORE_VERSION not set}"

if [ ! -x "$venv/bin/valcore" ] || [ "$(cat "$stamp" 2>/dev/null)" != "$version" ]; then
  mkdir -p "$home" && chmod 700 "$home"
  echo "valcore: provisioning environment (first run)…" >&2
  # Match the package's requires-python rather than pinning one release: uv
  # then reuses an interpreter the user already has instead of downloading a
  # toolchain, and brew installs keep working after 3.12 goes end-of-life.
  uv venv --python ">=3.11" "$venv" >&2
  # --refresh-package: $version is a release published moments before the formula bump,
  # so uv's cached index listing for valcore predates it and resolution fails with
  # "there is no version of valcore==$version". Scoped to this one package so the cache
  # still serves the dependency tree, which is what keeps provisioning fast.
  uv pip install --python "$venv/bin/python" --refresh-package valcore "valcore==$version" >&2
  printf '%s' "$version" > "$stamp"
fi

exec "$venv/bin/valcore" "$@"
