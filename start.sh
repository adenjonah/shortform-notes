#!/usr/bin/env bash
# One command to set up and launch reelnotes. Safe to re-run; each run refreshes yt-dlp.
#   ./start.sh            installs uv (if missing), Python and deps, then opens the setup UI
#   ./start.sh <url>      same setup, then imports the link from the terminal
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (Python package manager, one-time)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "Installing Python 3.12 and reelnotes dependencies (the first run takes about a minute)..."
uv python install 3.12 --quiet 2>/dev/null || true
uv sync --python 3.12 --extra all --extra local --upgrade-package yt-dlp --quiet

if [ "$#" -gt 0 ]; then
  exec uv run reelnotes "$@"
fi
exec uv run reelnotes web
