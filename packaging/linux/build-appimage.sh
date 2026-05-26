#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="meetings-minutes"
APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIST="$ROOT/dist"
APPDIR="$DIST/AppDir"

if [ ! -f "$DIST/$PROJECT_NAME" ]; then
  echo "Missing $DIST/$PROJECT_NAME — run pyinstaller first." >&2
  exit 1
fi

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"

cp "$DIST/$PROJECT_NAME" "$APPDIR/usr/bin/$PROJECT_NAME"
cp "$ROOT/packaging/linux/$PROJECT_NAME.desktop" "$APPDIR/$PROJECT_NAME.desktop"

if [ -f "$ROOT/assets/$PROJECT_NAME.png" ]; then
  cp "$ROOT/assets/$PROJECT_NAME.png" "$APPDIR/$PROJECT_NAME.png"
else
  echo "WARNING: no icon found at assets/$PROJECT_NAME.png — appimagetool will likely fail." >&2
fi

cat > "$APPDIR/AppRun" <<EOF
#!/usr/bin/env bash
HERE="\$(dirname "\$(readlink -f "\$0")")"
exec "\$HERE/usr/bin/$PROJECT_NAME" "\$@"
EOF
chmod +x "$APPDIR/AppRun"

"$APPIMAGETOOL" --no-appstream "$APPDIR" "$DIST/$PROJECT_NAME-x86_64.AppImage"
