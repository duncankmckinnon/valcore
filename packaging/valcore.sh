#!/bin/bash
set -euo pipefail
home="${VALCORE_HOME:-$HOME/.valcore}"
venv="$home/venv"
stamp="$venv/.version"
version="${VALCORE_VERSION:?VALCORE_VERSION not set}"

# --refresh-package: $version is a release published moments before the formula bump,
# so uv's cached index listing for valcore predates it and resolution fails with
# "there is no version of valcore==$version". Scoped to this one package so the cache
# still serves the dependency tree, which is what keeps provisioning fast.
install_pinned() {
  uv pip install --python "$venv/bin/python" --refresh-package valcore "valcore==$version" >&2
}

# Match the package's requires-python rather than pinning one release: uv then reuses an
# interpreter the user already has instead of downloading a toolchain, and brew installs
# keep working after 3.12 goes end-of-life. --clear because a half-written venv from an
# interrupted run would otherwise make `uv venv` abort; on a fresh path it is a no-op.
create_venv() {
  uv venv --clear --python ">=3.11" "$venv" >&2
}

if [ ! -x "$venv/bin/valcore" ] || [ "$(cat "$stamp" 2>/dev/null)" != "$version" ]; then
  mkdir -p "$home" && chmod 700 "$home"

  # Upgrades reuse the existing venv and let uv install just what changed. Recreating it
  # would be both slow (~115 packages) and, more importantly, broken: `uv venv` refuses a
  # path that already holds a venv, prompting at a TTY and failing outright without one --
  # so under `set -e` every post-upgrade run from a script or CI job died here.
  if [ -x "$venv/bin/python" ]; then
    # Braces are required: the ellipsis is a word character to bash's parser, so an
    # unbraced "$version…" is read as the variable "version…" and set -u aborts.
    echo "valcore: upgrading environment to ${version}…" >&2
  else
    echo "valcore: provisioning environment (first run)…" >&2
    create_venv
  fi

  # A reused venv can still be unusable -- most likely its interpreter no longer satisfies
  # the new release's requires-python. Rebuild once rather than leaving the user stuck.
  if ! install_pinned; then
    echo "valcore: environment is stale, rebuilding…" >&2
    create_venv
    install_pinned
  fi

  printf '%s' "$version" > "$stamp"
fi

exec "$venv/bin/valcore" "$@"
