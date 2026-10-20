#!/usr/bin/env bash
# Assemble the AppDir and run appimagetool.
# Expects: dist/EdenRoomHost (pyinstaller onefile), assets/icon.png,
# and $APPIMAGETOOL pointing at an appimagetool AppRun.
set -euxo pipefail

APPDIR=build/AppDir
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/icons/hicolor/256x256/apps"

cp dist/EdenRoomHost "$APPDIR/usr/bin/EdenRoomHost"
cp assets/icon.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/eden-room-host.png"
cp assets/icon.png "$APPDIR/eden-room-host.png"
cp assets/icon.png "$APPDIR/.DirIcon"

cat > "$APPDIR/eden-room-host.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Eden Room Host
Comment=Host Eden multiplayer rooms through playit.gg
Exec=EdenRoomHost
Icon=eden-room-host
Categories=Game;Network;
Terminal=false
EOF

cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
exec "$APPDIR/usr/bin/EdenRoomHost" "$@"
EOF
chmod +x "$APPDIR/AppRun" "$APPDIR/usr/bin/EdenRoomHost"

ARCH=x86_64 "${APPIMAGETOOL:-appimagetool}" "$APPDIR" Eden-Room-Host-x86_64.AppImage
