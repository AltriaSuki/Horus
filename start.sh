#!/usr/bin/env bash
# =============================================================================
# ADHD Engine + Frontend launcher (macOS / Linux)
#
# Double-click this file in Finder, or run from a shell:
#     ./start.sh
#
# Pass-through args:
#     ./start.sh --engine-only
#     ./start.sh --no-browser
# =============================================================================

set -eu

# Resolve the repo root from this script's location, regardless of cwd
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_ROOT"

# Prefer the venv python
VENV_PY="$REPO_ROOT/.venv/bin/python"

if [[ -x "$VENV_PY" ]]; then
    exec "$VENV_PY" "$REPO_ROOT/start.py" "$@"
fi

echo "[start.sh] no .venv found, falling back to system python"
if command -v python3.12 >/dev/null 2>&1; then
    exec python3.12 "$REPO_ROOT/start.py" "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 "$REPO_ROOT/start.py" "$@"
else
    echo "[start.sh] no python3 found on PATH" >&2
    exit 1
fi
