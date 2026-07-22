#!/usr/bin/env bash
set -euo pipefail

APP_ID="io.github.bulat.Transcriber"
APP_NAME="Транскрибатор"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'python3 не найден\n' >&2
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  printf 'ffmpeg/ffprobe не найдены. Установите ffmpeg через пакетный менеджер.\n' >&2
  exit 1
fi

python3 - <<'PY'
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, Gst
PY

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"

mkdir -p "$DESKTOP_DIR" "$ICON_DIR"
install -m 0644 "$ROOT_DIR/assets/transcriber.svg" "$ICON_DIR/$APP_ID.svg"

cat > "$DESKTOP_DIR/$APP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$APP_NAME
Comment=Локальная транскрибация видео и аудио
Exec=/usr/bin/python3 $ROOT_DIR/app.py
Icon=$APP_ID
Terminal=false
Categories=AudioVideo;Audio;Video;
StartupNotify=true
StartupWMClass=$APP_ID
EOF

chmod +x "$DESKTOP_DIR/$APP_ID.desktop"

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

if command -v xdg-desktop-menu >/dev/null 2>&1; then
  xdg-desktop-menu forceupdate >/dev/null 2>&1 || true
fi

printf '%s установлен. Ищите приложение в меню GNOME.\n' "$APP_NAME"
