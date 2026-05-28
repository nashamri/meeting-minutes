#!/usr/bin/env bash
# Download a pinned typst release and place it under vendor/typst/<plat>/.
# The PyInstaller spec picks it up and ships it inside the app bundle.
#
# Usage:
#   scripts/fetch-typst.sh                        # auto-detect host triple
#   scripts/fetch-typst.sh aarch64-apple-darwin   # explicit (used by CI matrix)
#
# Override the version with TYPST_VERSION=<x.y.z>. Bump TYPST_DEFAULT_VERSION
# in one place when you want to roll forward.
set -euo pipefail

TYPST_DEFAULT_VERSION="0.13.1"
TYPST_VERSION="${TYPST_VERSION:-$TYPST_DEFAULT_VERSION}"

triple="${1:-}"
if [ -z "$triple" ]; then
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os" in
    Linux)   triple="${arch}-unknown-linux-musl" ;;
    Darwin)  triple="${arch}-apple-darwin" ;;
    MINGW*|MSYS*|CYGWIN*) triple="x86_64-pc-windows-msvc" ;;
    *) echo "unsupported OS: $os" >&2; exit 1 ;;
  esac
fi

case "$triple" in
  *windows*) plat_dir="windows"; exe="typst.exe"; archive_ext="zip" ;;
  *darwin*)  plat_dir="macos";   exe="typst";     archive_ext="tar.xz" ;;
  *linux*)   plat_dir="linux";   exe="typst";     archive_ext="tar.xz" ;;
  *) echo "unrecognized triple: $triple" >&2; exit 1 ;;
esac

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST_DIR="$ROOT/vendor/typst/$plat_dir"
mkdir -p "$DEST_DIR"

url="https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-${triple}.${archive_ext}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Fetching $url"
curl -fsSL -o "$tmp/archive.$archive_ext" "$url"

if [ "$archive_ext" = "zip" ]; then
  if command -v unzip >/dev/null; then
    unzip -q "$tmp/archive.zip" -d "$tmp"
  else
    # Windows runners and many minimal images ship tar with zip support.
    (cd "$tmp" && tar -xf archive.zip)
  fi
else
  tar -xf "$tmp/archive.$archive_ext" -C "$tmp"
fi

src="$tmp/typst-${triple}/$exe"
if [ ! -f "$src" ]; then
  echo "expected $src after extraction; archive layout may have changed" >&2
  exit 1
fi

cp "$src" "$DEST_DIR/$exe"
chmod +x "$DEST_DIR/$exe"
echo "Vendored typst v${TYPST_VERSION} → $DEST_DIR/$exe"
