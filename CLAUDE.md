# CLAUDE.md

This file is for future Claude Code sessions working in this repository.

## Project Location

Repository path on this machine:

```text
/home/bulat/Projects/transcriber
```

GitHub repository:

```text
https://github.com/bulatmaster/transcriber
```

## Project Summary

`Транскрибатор` is a local GNOME desktop application for browsing video/audio files, previewing them, transcribing them locally on CPU, and keeping per-file notes.

Primary goals:

- Feel like a native GNOME app.
- Work locally without cloud transcription.
- Avoid writing any service/intermediate files into the source media folder.
- Keep transcripts and notes attached to files even if the original media file is renamed.
- Be deployable on another Linux/GNOME machine from GitHub with `./install.sh`.

## Tech Stack

- Python 3.
- GTK4, libadwaita and PyGObject for UI.
- GStreamer with `gtk4paintablesink` for in-app media preview.
- `ffmpeg`/`ffprobe` for media metadata and extracting audio from video.
- `faster-whisper` for local CPU transcription.
- SQLite for local metadata, transcripts, settings and notes.

Important implementation detail:

- The app is launched with system `python3`, because GTK/libadwaita GI bindings are usually installed system-wide.
- `app.py` prepends `.venv/lib/python*/site-packages` to `sys.path` so pip dependencies like `faster-whisper` are still loaded from the project venv.

## Important Files

- `app.py` - main application code.
- `requirements.txt` - pip dependencies for the project venv.
- `install.sh` - creates `.venv`, installs pip dependencies, installs GNOME desktop entry and icon.
- `README.md` - user-facing installation, usage and troubleshooting docs.
- `assets/transcriber.svg` - app icon source.
- `LICENSE` - MIT license.
- `.gitignore` - ignores `.venv`, caches and Python bytecode.

## Runtime Data

The application stores runtime data here:

```text
~/.local/share/transcriber
```

This includes:

- SQLite database: `transcriber.sqlite3`.
- Extracted audio: `audio/`.
- Video thumbnails: `thumbs/`.

Do not store generated data in the user's source media folder.

GNOME integration files installed by `install.sh`:

```text
~/.local/share/applications/io.github.bulat.Transcriber.desktop
~/.local/share/icons/hicolor/scalable/apps/io.github.bulat.Transcriber.svg
```

Application ID:

```text
io.github.bulat.Transcriber
```

Keep this app id stable. It affects GNOME menu, dock icon and Alt-Tab name matching.

## Setup Commands

Install system dependencies first. See `README.md` for distro-specific commands.

Local install from repository root:

```bash
./install.sh
```

Manual setup:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Run app directly:

```bash
python3 app.py
```

## Verification Commands

Run these after code changes:

```bash
python3 -m py_compile app.py
python3 -c "import app; print('ok')"
bash -n install.sh
```

Check GTK/libadwaita/GStreamer availability:

```bash
python3 - <<'PY'
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gst', '1.0')
from gi.repository import Gtk, Adw, Gst
print('GTK/libadwaita/GStreamer OK')
PY
```

Check ffmpeg:

```bash
ffmpeg -version
ffprobe -version
```

## Current Architecture Notes

`app.py` is intentionally a single-file app for now.

Main pieces:

- `MediaFile` dataclass: normalized media item shown in the UI.
- `Store`: SQLite persistence for settings, transcripts and notes.
- `Transcriber`: background worker using `ffmpeg` and `faster-whisper`.
- `Player`: GStreamer media preview widget with seek and speed controls.
- `MediaRow`: one row in the left file list.
- `MainWindow`: libadwaita window, file scanning, tabs, notes, context menu actions.
- `TranscriberApp`: application entry point and GNOME app id.

## File Scanning Behavior

Supported media extensions are defined near the top of `app.py`:

- `VIDEO_EXT`
- `AUDIO_EXT`

Scanning rules:

- Only direct files in the selected folder are shown; subfolders are not scanned.
- Files are sorted from newest to oldest by file creation/change time.
- Problematic files are skipped individually so one bad file does not blank the whole list.
- Each file gets a stable content-based id using size plus sampled chunks of file data.
- That id lets transcripts and notes survive manual file renames.

## Transcription Behavior

For video files:

1. Extract audio using `ffmpeg` to `~/.local/share/transcriber/audio/<file_id>.wav`.
2. Convert to mono 16 kHz WAV.
3. Transcribe extracted audio.

For audio files:

1. Convert/reuse a working WAV if needed.
2. Transcribe audio.

Whisper settings are chosen for CPU-only laptops with limited RAM:

- model: `base`
- device: `cpu`
- compute type: `int8`
- threads: up to 4
- `beam_size=1`
- `vad_filter=True`
- `condition_on_previous_text=False`

The transcript saved to the database includes a header:

```text
Расшифровка (видео) - filename.mp4
Дата файла: 22.07.2026 15:30

...
```

## Notes Behavior

Notes are saved automatically on `Gtk.TextBuffer.changed` and flushed again on window close. Keep this behavior: the user expects notes to persist while typing.

## UI Notes

- Use GTK4/libadwaita patterns, not Qt.
- Follow the system light/dark theme; do not add a custom theme switcher unless explicitly requested.
- Keep the tabs above the preview/content area.
- The transcription action is centered on the `Расшифровка` tab when no transcript exists.
- The file list is on the left; detail tabs are on the right.
- Right-click context menu on a file includes transcribe/open transcript, rename, delete.

## Git/GitHub Workflow

The user asked that updates also be pushed to GitHub.

For future changes:

```bash
git status --short
git diff
python3 -m py_compile app.py
python3 -c "import app; print('ok')"
bash -n install.sh
git add <changed-files>
git commit -m "Concise commit message"
git push
```

Only stage intended files. Do not commit `.venv`, `__pycache__`, local runtime data or generated media.

## Common Pitfalls

- Do not use venv Python as the desktop entry executable unless system GI modules are also available there. The current desktop entry intentionally uses `/usr/bin/python3`.
- Do not write extracted audio, thumbnails or database files into the selected media folder.
- Do not change `APP_ID` unless you also migrate desktop entry/icon behavior.
- Be careful with scan changes: a single unreadable or partially copied file must not break the whole list.
- Be careful with GStreamer seek logic: avoid issuing two seek operations for one user seek, otherwise playback may jump back.
- If the app does not appear in GNOME menu after desktop changes, run `./install.sh` and possibly log out/in.

## Current Remote

```bash
git remote -v
```

Expected:

```text
origin  https://github.com/bulatmaster/transcriber.git (fetch)
origin  https://github.com/bulatmaster/transcriber.git (push)
```
