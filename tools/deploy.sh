#!/usr/bin/env bash
# Publish the Toutur installer to the static web directory served at toutur.jitinnair.com.
#
#   bash tools/deploy.sh            # build + publish + verify
#   bash tools/deploy.sh --dry-run  # show what it would publish
#
# What it produces in $WWW_DIR:
#   toutur-<version>.tar.gz     the pack (install.py, scripts/, SKILL.md, TUI)
#   SHA256SUMS                 checksums the archive hash
#   install.sh                 the one-command bootstrap
#   toutur-icon.png             the repo icon
#
# Served by toolr-www.service (127.0.0.1:8030) behind the existing cloudflared
# tunnel. Nothing here touches the tunnel or DNS.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WWW_DIR="${WWW_DIR:-$HOME/Work/toolr-www}"
PORT="${PORT:-8030}"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

say() { printf '\033[1m%s\033[0m\n' "$*"; }

VERSION="$(date -u +%Y%m%d)"
HASH="$(git -C "$REPO" rev-parse --short=8 HEAD 2>/dev/null || echo nohash)"
STAMP="${VERSION}-${HASH}"
say "Publishing Toutur ${STAMP} -> $WWW_DIR"

PAYLOAD=(SKILL.md install.py install.sh install.ps1 toolr_install.py toolr_tui.py toolr_anim.py landing scripts references tools README.md assets/toutur-icon.png)

ARCHIVE="$WWW_DIR/toutur-${STAMP}.tar.gz"
mkdir -p "$WWW_DIR"
TARLIST="$(mktemp)"
for f in "${PAYLOAD[@]}"; do
  [ -e "$REPO/$f" ] || { echo "missing payload: $f" >&2; exit 1; }
  echo "$f" >> "$TARLIST"
done
if [ "$DRY" = 1 ]; then
  say "--dry-run: would publish"
  cat "$TARLIST" | sed 's/^/    /'
  echo "    -> ${ARCHIVE}"
  rm -f "$TARLIST"
  exit 0
fi
tar -czf "$ARCHIVE" -C "$REPO" -T "$TARLIST"
rm -f "$TARLIST"

(
  cd "$WWW_DIR"
  sha256sum "toutur-${STAMP}.tar.gz" > SHA256SUMS
  cp "$REPO/install.sh" install.sh
  chmod +x install.sh
  cp "$REPO/install.ps1" install.ps1
  cp "$REPO/assets/toutur-icon.png" toutur-icon.png
  cp "$REPO"/branding/favicon/*.png "$REPO"/branding/favicon/favicon.ico .
  # the landing page must exist as browsable files, not just inside the
  # tarball — serve-www maps / to /landing/index.html
  rm -rf "$WWW_DIR/landing"
  cp -r "$REPO/landing" "$WWW_DIR/landing"
)
# Content-stamped checksum copy: the file NAME changes every deploy, so it
# matches deploy.sh's IMMUTABLE regex and the CDN caches it forever — always
# correct, immune to the mutable-URL staleness incident (2026-10-08: the old
# serve-www sent max-age=1y for SHA256SUMS and Cloudflare pinned a stale copy;
# origin now sends no-store, but already-cached edge objects can't be purged
# without a CF API token, so verifiers should prefer checksums-$STAMP.txt).
cp "$WWW_DIR/SHA256SUMS" "$WWW_DIR/checksums-${STAMP}.txt"

say "Published:"
ls -la "$WWW_DIR"
say "Verify: curl -fsSL http://127.0.0.1:${PORT}/install.sh | head -3"
