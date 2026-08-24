#!/usr/bin/env bash
# One command to set up and run reelnotes. Safe to re-run; each run refreshes yt-dlp.
#   ./start.sh            first run: asks whether to set up in the browser or in this terminal
#   ./start.sh web        open the browser setup and import page
#   ./start.sh setup      run the terminal setup wizard
#   ./start.sh <url>      import a link from the terminal
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (Python package manager, one time)."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "Installing Python 3.12 and reelnotes dependencies. The first run takes about a minute."
uv python install 3.12 --quiet 2>/dev/null || true
uv sync --python 3.12 --extra all --extra local --upgrade-package yt-dlp --quiet

if [ "$#" -gt 0 ]; then
  exec uv run reelnotes "$@"
fi

CONFIG="${REELNOTES_CONFIG:-$HOME/.config/reelnotes/config.env}"
if [ -f "$CONFIG" ] || [ ! -t 0 ]; then
  exec uv run reelnotes web
fi

echo
echo "How do you want to set up reelnotes?"
echo "  1) In the browser (recommended if you are not used to the terminal)"
echo "  2) In this terminal"
read -r -p "Choose 1 or 2 [1]: " choice
case "${choice:-1}" in
  2) exec uv run reelnotes setup ;;
  *) exec uv run reelnotes web ;;
esac
