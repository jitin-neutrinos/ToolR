#!/bin/sh
# Toutur one-line installer — curl -fsSL https://toutur.jitinnair.com/install.sh | bash
# Detects harnesses, clones/reuses the repo, runs the TUI installer.
set -eu

REPO_URL="${TOOLR_REPO:-https://github.com/jitin-neutrinos/Toutur}"
DIR="${TOOLR_DIR:-$HOME/toutur}"

echo ""
echo "  Toutur — route every prompt to the right skill."
echo "  repo: $REPO_URL"
echo ""

# 1. Get the code
if [ -d "$DIR/.git" ]; then
  echo "▸ Found existing copy at $DIR — pulling latest"
  git -C "$DIR" pull --ff-only >/dev/null 2>&1 || echo "  (pull failed, using existing copy)"
elif command -v git >/dev/null 2>&1; then
  echo "▸ Cloning repository"
  git clone --depth 1 "$REPO_URL" "$DIR"
else
  echo "! git not found. Install git and retry, or download the zip from $REPO_URL" >&2
  exit 1
fi

# 2. Detect + install (install.py is idempotent; toolr_install.py adds the TUI)
echo ""
python3 "$DIR/install.py" --check || true
echo ""
exec python3 "$DIR/install.py" "$@"
