import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from summary import clear_custom_key, has_custom_key, read_api_key, save_api_key, summarize_call

VENV_SITE = Path(__file__).resolve().parent / ".venv" / "lib"
for site in VENV_SITE.glob("python*/site-packages"):
    sys.path.insert(0, str(site))

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gst", "1.0")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gst, Gtk

APP_ID = "io.github.bulat.Transcriber"
APP_DIR = Path.home() / ".local" / "share" / "transcriber"
DB_PATH = APP_DIR / "transcriber.sqlite3"
AUDIO_DIR = APP_DIR / "audio"
THUMB_DIR = APP_DIR / "thumbs"

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".amr"}
MEDIA_EXT = VIDEO_EXT | AUDIO_EXT


@dataclass
class MediaFile:
    file_id: str
    path: Path
    kind: str
    duration: float
    created_at: float
    transcript: str
    notes: str
    summary: str


def ensure_dirs():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)


def run_json(command):
    try:
        out = subprocess.check_output(command, stderr=subprocess.DEVNULL, text=True)
        return json.loads(out)
    except Exception:
        return None


def media_duration(path: Path) -> float:
    data = run_json(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)])
    try:
        return float(data["format"]["duration"])
    except Exception:
        return 0.0


def fmt_duration(seconds: float) -> str:
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def file_created_at(path: Path) -> float:
    stat = path.stat()
    return getattr(stat, "st_birthtime", stat.st_ctime)


def fmt_datetime(timestamp: float) -> str:
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))


def transcript_header(media: MediaFile) -> str:
    kind = "видео" if media.kind == "video" else "аудио"
    return f"Расшифровка ({kind}) - {media.path.name}\nДата файла: {fmt_datetime(media.created_at)}"


def stable_file_id(path: Path) -> str:
    stat = path.stat()
    sample = b""
    with path.open("rb") as f:
        sample += f.read(1024 * 1024)
        if stat.st_size > 1024 * 1024:
            f.seek(max(0, stat.st_size // 2 - 512 * 1024))
            sample += f.read(1024 * 1024)
        if stat.st_size > 2 * 1024 * 1024:
            f.seek(max(0, stat.st_size - 1024 * 1024))
            sample += f.read(1024 * 1024)
    h = hashlib.sha256()
    h.update(str(stat.st_size).encode())
    h.update(sample)
    return h.hexdigest()


def thumbnail_path(media: MediaFile) -> Path | None:
    if media.kind != "video":
        return None
    thumb = THUMB_DIR / f"{media.file_id}.jpg"
    if not thumb.exists() and shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "00:00:01", "-i", str(media.path), "-frames:v", "1", "-vf", "scale=160:-1", str(thumb)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return thumb if thumb.exists() else None


class Store:
    def __init__(self):
        ensure_dirs()
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.execute("pragma journal_mode=wal")
        self.conn.executescript(
            """
            create table if not exists files(
                id text primary key,
                last_path text,
                kind text,
                duration real default 0,
                transcript text default '',
                notes text default '',
                summary text default '',
                updated_at real
            );
            create table if not exists settings(key text primary key, value text);
            """
        )
        if "summary" not in {row[1] for row in self.conn.execute("pragma table_info(files)")}:
            self.conn.execute("alter table files add column summary text default ''")
            self.conn.commit()

    def enrich(self, file_id, path, kind, duration):
        row = self.conn.execute("select transcript, notes, summary from files where id=?", (file_id,)).fetchone()
        transcript, notes, summary = row if row else ("", "", "")
        self.conn.execute(
            """
            insert into files(id,last_path,kind,duration,transcript,notes,updated_at)
            values(?,?,?,?,?,?,?)
            on conflict(id) do update set last_path=excluded.last_path, kind=excluded.kind,
                duration=excluded.duration, updated_at=excluded.updated_at
            """,
            (file_id, str(path), kind, duration, transcript or "", notes or "", time.time()),
        )
        self.conn.commit()
        return MediaFile(file_id, path, kind, duration, file_created_at(path), transcript or "", notes or "", summary or "")

    def set_transcript(self, file_id, text):
        self.conn.execute("update files set transcript=?, updated_at=? where id=?", (text, time.time(), file_id))
        self.conn.commit()

    def set_notes(self, file_id, text):
        self.conn.execute("update files set notes=?, updated_at=? where id=?", (text, time.time(), file_id))
        self.conn.commit()

    def set_summary(self, file_id, summary):
        row = self.conn.execute("select notes, summary from files where id=?", (file_id,)).fetchone()
        if not row:
            return ""
        notes, old_summary = row
        notes = notes or ""
        if old_summary:
            old_line = f"Саммари: {old_summary}"
            if notes == old_line:
                notes = ""
            elif notes.startswith(old_line + "\n\n"):
                notes = notes[len(old_line) + 2:]
        notes = f"Саммари: {summary}\n\n{notes}" if notes else f"Саммари: {summary}"
        self.conn.execute(
            "update files set notes=?, summary=?, updated_at=? where id=?",
            (notes, summary, time.time(), file_id),
        )
        self.conn.commit()
        return notes

    def get_setting(self, key, default=""):
        row = self.conn.execute("select value from settings where key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key, value):
        self.conn.execute("insert into settings(key,value) values(?,?) on conflict(key) do update set value=excluded.value", (key, value))
        self.conn.commit()


class Transcriber:
    def __init__(self, media, on_progress, on_done, on_fail):
        self.media = media
        self.on_progress = on_progress
        self.on_done = on_done
        self.on_fail = on_fail
        self.stop = threading.Event()
        self.process = None

    def cancel(self):
        self.stop.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def progress(self, value, label):
        GLib.idle_add(self.on_progress, self.media.file_id, value, label)

    def run(self):
        try:
            if shutil.which("ffmpeg") is None:
                raise RuntimeError("ffmpeg не найден")
            source = self.media.path
            audio = AUDIO_DIR / f"{self.media.file_id}.wav"
            if self.media.kind == "video" or not audio.exists():
                self.progress(5, "извлечение аудио")
                self.process = subprocess.Popen(
                    ["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(audio)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                while self.process.poll() is None:
                    if self.stop.is_set() or not source.exists():
                        self.cancel()
                        raise RuntimeError("исходный файл удален, транскрибация остановлена")
                    time.sleep(0.2)
                if self.process.returncode != 0:
                    raise RuntimeError("не удалось извлечь аудио")
            if self.stop.is_set() or not source.exists():
                raise RuntimeError("исходный файл удален, транскрибация остановлена")
            self.progress(15, "загрузка модели")
            from faster_whisper import WhisperModel

            model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=max(1, min(4, os.cpu_count() or 2)))
            self.progress(25, "транскрибация")
            segments, _ = model.transcribe(
                str(audio),
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 600, "speech_pad_ms": 250},
                beam_size=1,
                best_of=1,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.4,
            )
            parts = []
            duration = max(1, self.media.duration)
            for seg in segments:
                if self.stop.is_set() or not source.exists():
                    raise RuntimeError("исходный файл удален, транскрибация остановлена")
                text = seg.text.strip()
                if text:
                    parts.append(text)
                self.progress(min(95, 25 + int((seg.end / duration) * 70)), "транскрибация")
            GLib.idle_add(self.on_done, self.media.file_id, "\n".join(parts).strip())
        except Exception as exc:
            GLib.idle_add(self.on_fail, self.media.file_id, str(exc))


class Player(Gtk.Box):
    def __init__(self, show_in_folder):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.show_in_folder = show_in_folder
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.set_margin_start(4)
        self.set_margin_end(4)
        Gst.init(None)
        self.rate = 1.0
        self.duration = 0
        self.updating_position = False
        self.pipeline = Gst.ElementFactory.make("playbin", "player")
        self.tempo = Gst.ElementFactory.make("scaletempo", "voice-tempo")
        if self.tempo is None:
            raise RuntimeError("Для сохранения тональности голоса нужен плагин GStreamer scaletempo")
        self.pipeline.set_property("audio-filter", self.tempo)
        self.sink = Gst.ElementFactory.make("gtk4paintablesink", "sink")
        self.pipeline.set_property("video-sink", self.sink)
        paintable = self.sink.get_property("paintable")
        self.picture = Gtk.Picture.new_for_paintable(paintable)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_size_request(-1, 190)
        self.picture.set_vexpand(True)
        self.picture.add_css_class("viewer")
        self.append(self.picture)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.add_css_class("toolbar-card")
        self.play_btn = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.play_btn.connect("clicked", self.toggle)
        self.position = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.position.set_hexpand(True)
        self.position.set_draw_value(False)
        self.position.connect("value-changed", self.on_seek)
        self.time_label = Gtk.Label(label="00:00 / 00:00")
        self.speed = Gtk.DropDown.new_from_strings(["3x", "2x", "1.5x", "1x"])
        self.speed.set_selected(3)
        self.speed.connect("notify::selected", self.on_speed)
        self.folder_btn = Gtk.Button(label="Показать в папке", icon_name="document-open-symbolic")
        self.folder_btn.connect("clicked", lambda _b: self.show_in_folder())
        controls.append(self.play_btn)
        controls.append(self.position)
        controls.append(self.time_label)
        controls.append(self.speed)
        controls.append(self.folder_btn)
        self.append(controls)
        GLib.timeout_add(350, self.tick)

    def load(self, path: Path):
        self.pipeline.set_state(Gst.State.NULL)
        self.picture.set_visible(path.suffix.lower() in VIDEO_EXT)
        self.duration = 0
        self.updating_position = True
        self.position.set_value(0)
        self.updating_position = False
        self.time_label.set_text("00:00 / 00:00")
        self.pipeline.set_property("uri", path.as_uri())
        self.pipeline.set_state(Gst.State.PAUSED)
        self.play_btn.set_icon_name("media-playback-start-symbolic")

    def toggle(self, _button):
        _, state, _ = self.pipeline.get_state(0)
        if state == Gst.State.PLAYING:
            self.pipeline.set_state(Gst.State.PAUSED)
            self.play_btn.set_icon_name("media-playback-start-symbolic")
        else:
            self.pipeline.set_state(Gst.State.PLAYING)
            self.apply_rate()
            self.play_btn.set_icon_name("media-playback-pause-symbolic")

    def on_speed(self, _drop, _param):
        rates = [3.0, 2.0, 1.5, 1.0]
        self.rate = rates[self.speed.get_selected()]
        self.apply_rate()

    def apply_rate(self):
        ok, pos = self.pipeline.query_position(Gst.Format.TIME)
        if not ok:
            pos = 0
        self.seek_to(pos)

    def seek_to(self, pos):
        flags = Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE
        self.pipeline.seek(self.rate, Gst.Format.TIME, flags, Gst.SeekType.SET, int(pos), Gst.SeekType.NONE, -1)

    def on_seek(self, scale):
        if self.updating_position:
            return
        if self.duration:
            value = scale.get_value()
            self.seek_to(value / 100 * self.duration)

    def tick(self):
        okd, duration = self.pipeline.query_duration(Gst.Format.TIME)
        okp, pos = self.pipeline.query_position(Gst.Format.TIME)
        if okd and duration > 0:
            self.duration = duration
            if okp:
                self.updating_position = True
                self.position.set_value(pos / duration * 100)
                self.updating_position = False
                self.time_label.set_text(f"{fmt_duration(pos / Gst.SECOND)} / {fmt_duration(duration / Gst.SECOND)}")
        return True

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)


class MediaRow(Gtk.ListBoxRow):
    def __init__(self, media: MediaFile, window):
        super().__init__()
        self.media = media
        self.window = window
        self.progress = Gtk.ProgressBar()
        self.progress.set_visible(False)
        self.title = Gtk.Label(label=media.path.name, xalign=0)
        self.title.set_ellipsize(3)
        self.title.set_max_width_chars(34)
        self.title.add_css_class("heading")
        self.meta = Gtk.Label(xalign=0)
        self.meta.set_ellipsize(3)
        self.meta.set_max_width_chars(34)
        self.meta.add_css_class("dim-label")
        self.summary_label = Gtk.Label(xalign=0)
        self.summary_label.set_ellipsize(3)
        self.summary_label.set_max_width_chars(34)
        self.summary_label.set_single_line_mode(True)
        self.update_meta()

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        thumb = thumbnail_path(media)
        if thumb:
            preview = Gtk.Picture()
            preview.set_file(Gio.File.new_for_path(str(thumb)))
        else:
            preview = Gtk.Image.new_from_icon_name(
                "audio-x-generic-symbolic" if media.kind == "audio" else "video-x-generic-symbolic"
            )
            preview.set_pixel_size(32)
        preview.set_size_request(88, 52)
        preview.add_css_class("thumb")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        text.append(self.title)
        text.append(self.meta)
        text.append(self.summary_label)
        text.append(self.progress)
        box.append(preview)
        box.append(text)
        text.set_hexpand(True)
        self.set_child(box)

        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self.show_menu)
        self.add_controller(click)

    def update_meta(self):
        status = "транскрибировано" if self.media.transcript else "нет расшифровки"
        self.meta.set_text(f"{self.media.kind} · {fmt_duration(self.media.duration)} · {fmt_datetime(self.media.created_at)} · {status}")
        self.summary_label.set_text(self.media.summary)
        self.summary_label.set_tooltip_text(self.media.summary or None)
        self.summary_label.set_visible(bool(self.media.summary))

    def set_progress(self, value, label):
        self.progress.set_visible(True)
        self.progress.set_fraction(value / 100)
        self.meta.set_text(f"{self.media.kind} · {fmt_duration(self.media.duration)} · {fmt_datetime(self.media.created_at)} · {label}")

    def clear_progress(self):
        self.progress.set_visible(False)
        self.update_meta()

    def show_menu(self, _gesture, _n, x, y):
        self.window.listbox.select_row(self)
        pop = Gtk.Popover()
        pop.set_parent(self)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        pop.set_pointing_to(rect)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        actions = [
            ("Открыть расшифровку" if self.media.transcript else "Транскрибировать", self.window.open_or_transcribe),
            ("Переименовать", self.window.rename_current),
            ("Удалить", self.window.delete_current),
        ]
        for label, callback in actions:
            btn = Gtk.Button(label=label)
            btn.set_halign(Gtk.Align.FILL)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda _b, cb=callback, p=pop: (p.popdown(), cb()))
            box.append(btn)
        pop.set_child(box)
        pop.popup()


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Транскрибатор")
        self.set_default_size(1220, 780)
        self.store = Store()
        self.media = []
        self.rows = {}
        self.active = {}
        self.current = None
        self.current_loaded = None
        self.scan_signature = None
        self.notes_blocked = False
        self.summaries_in_progress = set()
        self.settings_window = None
        self.folder_settings_row = None
        self.connect("close-request", self.on_close_request)

        header = Adw.HeaderBar()
        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Настройки")
        settings_btn.connect("clicked", self.open_settings)
        header.pack_end(settings_btn)

        self.folder_label = Gtk.Label(label=self.store.get_setting("folder", str(Path.home())))
        self.folder_label.set_ellipsize(3)
        self.folder_label.set_max_width_chars(60)
        self.folder_label.add_css_class("dim-label")
        header.set_title_widget(self.folder_label)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(390)
        self.listbox = Gtk.ListBox()
        self.listbox.add_css_class("boxed-list")
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self.select_row)
        scroller = Gtk.ScrolledWindow()
        scroller.set_child(self.listbox)
        scroller.set_min_content_width(330)
        scroller.set_margin_top(12)
        scroller.set_margin_bottom(12)
        scroller.set_margin_start(12)
        scroller.set_margin_end(6)
        paned.set_start_child(scroller)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_vexpand(True)
        content.set_margin_top(8)
        content.set_margin_bottom(8)
        content.set_margin_start(6)
        content.set_margin_end(12)
        content.append(self.build_preview_section())
        content.append(self.build_transcript_section())
        content.append(self.build_notes_section())
        paned.set_end_child(content)
        toolbar.set_content(paned)
        self.set_content(toolbar)

        GLib.timeout_add_seconds(5, self.scan)
        self.scan()

    def build_preview_section(self):
        self.player = Player(self.show_current_in_folder)
        self.player.set_vexpand(True)
        return self.player

    def show_current_in_folder(self):
        if not self.current:
            return
        try:
            if shutil.which("nautilus"):
                subprocess.Popen(["nautilus", "--select", str(self.current.path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(self.current.path.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            self.toast(str(exc))

    def build_transcript_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_vexpand(True)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Расшифровка", xalign=0)
        title.add_css_class("heading")
        toolbar.append(title)
        self.transcript = Gtk.TextView(editable=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.transcript.set_vexpand(True)
        self.transcript.add_css_class("card")
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        scroller.set_min_content_height(112)
        scroller.set_child(self.transcript)
        self.transcribe_btn = Gtk.Button(label="Транскрибировать")
        self.transcribe_btn.add_css_class("suggested-action")
        self.transcribe_btn.connect("clicked", lambda _b: self.start_current())
        self.main_progress = Gtk.ProgressBar()
        self.main_progress.set_visible(False)
        self.main_progress.set_size_request(100, -1)
        self.status = Gtk.Label(xalign=0)
        self.status.add_css_class("dim-label")
        self.status.set_ellipsize(3)
        self.status.set_hexpand(True)
        toolbar.append(self.transcribe_btn)
        toolbar.append(self.main_progress)
        toolbar.append(self.status)
        box.append(toolbar)
        box.append(scroller)
        return box

    def build_notes_section(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_vexpand(True)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Заметки", xalign=0)
        title.add_css_class("heading")
        toolbar.append(title)
        self.summary_btn = Gtk.Button(label="Создать саммари")
        self.summary_btn.connect("clicked", lambda _b: self.start_current_summary())
        toolbar.append(self.summary_btn)
        self.notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.notes.set_vexpand(True)
        self.notes.add_css_class("card")
        self.notes.get_buffer().connect("changed", self.save_notes)
        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_hexpand(True)
        scroller.set_min_content_height(112)
        scroller.set_child(self.notes)
        box.append(toolbar)
        box.append(scroller)
        return box

    def open_settings(self, _button=None):
        if self.settings_window:
            self.settings_window.present()
            return
        window = Adw.PreferencesWindow(transient_for=self, title="Настройки")
        window.set_default_size(600, 420)
        window.connect("close-request", self.on_settings_close)
        page = Adw.PreferencesPage()

        media_group = Adw.PreferencesGroup(title="Записи")
        folder_row = Adw.ActionRow(title="Папка записей", subtitle=self.store.get_setting("folder", str(Path.home())))
        folder_btn = Gtk.Button(label="Выбрать")
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.connect("clicked", self.choose_folder)
        folder_row.add_suffix(folder_btn)
        media_group.add(folder_row)
        self.folder_settings_row = folder_row

        auto_row = Adw.ActionRow(title="Автоматически транскрибировать новые записи")
        auto_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        auto_switch.set_active(self.store.get_setting("auto", "0") == "1")
        auto_switch.connect("notify::active", self.on_auto_toggled)
        auto_row.add_suffix(auto_switch)
        auto_row.set_activatable_widget(auto_switch)
        media_group.add(auto_row)
        page.add(media_group)

        key_group = Adw.PreferencesGroup(
            title="Саммари OpenAI",
            description="После локальной транскрибации в OpenAI отправляется только текст расшифровки.",
        )
        key_row = Adw.ActionRow(title="Ключ OpenAI API")
        self.key_entry = Gtk.PasswordEntry(placeholder_text="Вставьте новый ключ")
        self.key_entry.set_size_request(240, -1)
        self.key_entry.set_valign(Gtk.Align.CENTER)
        key_row.add_suffix(self.key_entry)
        key_group.add(key_row)
        self.key_status_row = Adw.ActionRow(title="Источник ключа")
        key_group.add(self.key_status_row)
        key_actions = Adw.ActionRow()
        save_btn = Gtk.Button(label="Сохранить ключ")
        save_btn.add_css_class("suggested-action")
        save_btn.set_valign(Gtk.Align.CENTER)
        save_btn.connect("clicked", self.on_save_key)
        reset_btn = Gtk.Button(label="Использовать ключ из ~/keys")
        reset_btn.set_valign(Gtk.Align.CENTER)
        reset_btn.connect("clicked", self.on_reset_key)
        key_actions.add_suffix(reset_btn)
        key_actions.add_suffix(save_btn)
        key_group.add(key_actions)
        page.add(key_group)
        window.add(page)
        self.settings_window = window
        self.update_key_status()
        window.present()

    def on_settings_close(self, _window):
        self.settings_window = None
        self.folder_settings_row = None
        return False

    def update_key_status(self):
        if has_custom_key():
            source = "Сохранён в настройках приложения"
        elif read_api_key():
            source = "~/keys/openai_key.txt"
        else:
            source = "Ключ не задан"
        self.key_status_row.set_subtitle(source)

    def on_save_key(self, _button):
        try:
            save_api_key(self.key_entry.get_text())
        except (OSError, ValueError) as exc:
            self.toast(str(exc))
            return
        self.key_entry.set_text("")
        self.update_key_status()

    def on_reset_key(self, _button):
        try:
            clear_custom_key()
        except OSError as exc:
            self.toast(str(exc))
            return
        self.key_entry.set_text("")
        self.update_key_status()

    def on_auto_toggled(self, switch, _param):
        enabled = switch.get_active()
        self.store.set_setting("auto", "1" if enabled else "0")
        if enabled:
            self.scan()

    def choose_folder(self, _button):
        dialog = Gtk.FileDialog(title="Выберите папку")
        dialog.select_folder(self.settings_window or self, None, self.on_folder_chosen)

    def on_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result).get_path()
        except Exception:
            return
        self.store.set_setting("folder", folder)
        self.folder_label.set_text(folder)
        if self.folder_settings_row:
            self.folder_settings_row.set_subtitle(folder)
        self.scan()

    def scan(self):
        root = Path(self.store.get_setting("folder", str(Path.home())))
        if not root.exists():
            return True
        selected = self.current.file_id if self.current else None
        items = []
        skipped = 0
        for p in root.iterdir():
            try:
                if not p.is_file() or p.suffix.lower() not in MEDIA_EXT:
                    continue
                kind = "video" if p.suffix.lower() in VIDEO_EXT else "audio"
                items.append(self.store.enrich(stable_file_id(p), p, kind, media_duration(p)))
            except Exception:
                skipped += 1
                continue
        items.sort(key=lambda m: m.created_at, reverse=True)
        signature = tuple((m.file_id, str(m.path), m.kind, int(m.duration), int(m.created_at), bool(m.transcript), m.summary) for m in items)
        self.media = items
        if signature != self.scan_signature:
            self.scan_signature = signature
            self.render_list(selected)
        if self.store.get_setting("auto", "0") == "1":
            for mf in self.media:
                if not mf.transcript and mf.file_id not in self.active:
                    self.start_transcription(mf)
        for fid, worker in list(self.active.items()):
            if not any(m.file_id == fid and m.path.exists() for m in self.media):
                worker.cancel()
        if skipped and not self.current:
            self.status.set_text(f"Пропущено файлов при сканировании: {skipped}")
        return True

    def render_list(self, selected_id=None):
        while row := self.listbox.get_row_at_index(0):
            self.listbox.remove(row)
        self.rows.clear()
        selected_row = None
        for mf in self.media:
            row = MediaRow(mf, self)
            self.listbox.append(row)
            self.rows[mf.file_id] = row
            if mf.file_id == selected_id:
                selected_row = row
        if selected_row:
            self.listbox.select_row(selected_row)
        elif self.media:
            self.listbox.select_row(self.listbox.get_row_at_index(0))

    def select_row(self, _list, row):
        if not row:
            return
        previous = self.current
        self.current = row.media
        if not previous or previous.file_id != self.current.file_id or previous.path != self.current.path or self.current_loaded != self.current.path:
            self.player.load(self.current.path)
            self.current_loaded = self.current.path
        self.set_text(self.transcript, self.current.transcript or "")
        self.notes_blocked = True
        self.set_text(self.notes, self.current.notes or "")
        self.notes_blocked = False
        running = self.current.file_id in self.active
        self.transcribe_btn.set_visible(not bool(self.current.transcript) and not running)
        self.main_progress.set_visible(running)
        self.status.set_text("транскрибация идет" if running else "")
        self.update_summary_button()

    def update_summary_button(self):
        self.summary_btn.set_visible(bool(
            self.current and self.current.transcript and not self.current.summary
            and self.current.file_id not in self.summaries_in_progress
        ))

    def set_text(self, text_view, value):
        text_view.get_buffer().set_text(value)

    def buffer_text(self, text_view):
        buf = text_view.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    def save_notes(self, _buffer):
        if self.current and not self.notes_blocked:
            self.current.notes = self.buffer_text(self.notes)
            self.store.set_notes(self.current.file_id, self.current.notes)

    def flush_notes(self):
        if self.current:
            self.current.notes = self.buffer_text(self.notes)
            self.store.set_notes(self.current.file_id, self.current.notes)

    def open_or_transcribe(self):
        if not self.current:
            return
        if self.current.transcript:
            self.transcript.grab_focus()
        else:
            self.start_current()

    def start_current(self):
        if self.current:
            self.start_transcription(self.current)

    def start_transcription(self, mf):
        if mf.file_id in self.active:
            return
        worker = Transcriber(mf, self.on_progress, self.on_finished, self.on_failed)
        self.active[mf.file_id] = worker
        worker.start()
        self.on_progress(mf.file_id, 1, "ожидание")

    def on_progress(self, fid, value, label):
        row = self.rows.get(fid)
        if row:
            row.set_progress(value, label)
        if self.current and self.current.file_id == fid:
            self.main_progress.set_visible(True)
            self.main_progress.set_fraction(value / 100)
            self.status.set_text(label)
            self.transcribe_btn.set_visible(False)
        return False

    def on_finished(self, fid, text):
        self.active.pop(fid, None)
        media = next((m for m in self.media if m.file_id == fid), None)
        full_text = f"{transcript_header(media)}\n\n{text}" if media else text
        self.store.set_transcript(fid, full_text)
        for mf in self.media:
            if mf.file_id == fid:
                mf.transcript = full_text
        row = self.rows.get(fid)
        if row:
            row.media.transcript = full_text
            row.clear_progress()
        if self.current and self.current.file_id == fid:
            self.set_text(self.transcript, full_text)
            self.main_progress.set_visible(False)
            self.status.set_text("готово")
            self.transcribe_btn.set_visible(False)
            self.update_summary_button()
        if text.strip() and media:
            self.start_summary(media, text)
        return False

    def on_failed(self, fid, error):
        self.active.pop(fid, None)
        row = self.rows.get(fid)
        if row:
            row.clear_progress()
        if self.current and self.current.file_id == fid:
            self.main_progress.set_visible(False)
            self.status.set_text(error)
            self.transcribe_btn.set_visible(True)
        return False

    def start_current_summary(self):
        if self.current and self.current.transcript:
            body = self.current.transcript
            if body.startswith("Расшифровка ("):
                body = body.split("\n\n", 1)[-1]
            self.start_summary(self.current, body)

    def start_summary(self, media, text):
        fid = media.file_id
        if fid in self.summaries_in_progress or media.summary:
            return
        if not read_api_key():
            if self.current and self.current.file_id == fid:
                self.status.set_text("Добавьте ключ OpenAI в настройках")
            return
        self.summaries_in_progress.add(fid)
        if self.current and self.current.file_id == fid:
            self.status.set_text("создание саммари")
            self.update_summary_button()
        threading.Thread(target=self.summarize_in_background, args=(fid, text), daemon=True).start()

    def summarize_in_background(self, fid, text):
        try:
            summary = summarize_call(text)
        except Exception as exc:
            GLib.idle_add(self.on_summary_failed, fid, str(exc))
        else:
            GLib.idle_add(self.on_summary_done, fid, summary)

    def on_summary_done(self, fid, summary):
        self.summaries_in_progress.discard(fid)
        notes = self.store.set_summary(fid, summary)
        for media in self.media:
            if media.file_id == fid:
                media.summary = summary
                media.notes = notes
        row = self.rows.get(fid)
        if row:
            row.media.summary = summary
            row.media.notes = notes
            row.update_meta()
        if self.current and self.current.file_id == fid:
            self.current.summary = summary
            self.current.notes = notes
            self.notes_blocked = True
            self.set_text(self.notes, notes)
            self.notes_blocked = False
            self.status.set_text("готово")
            self.update_summary_button()
        return False

    def on_summary_failed(self, fid, error):
        self.summaries_in_progress.discard(fid)
        if self.current and self.current.file_id == fid:
            self.status.set_text(f"Саммари не создано: {error}")
            self.update_summary_button()
        return False

    def rename_current(self):
        if not self.current:
            return
        dialog = Adw.MessageDialog(transient_for=self, heading="Переименовать файл", body="Введите новое имя файла")
        entry = Gtk.Entry(text=self.current.path.name)
        entry.set_activates_default(True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("rename", "Переименовать")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.connect("response", self.on_rename_response, entry)
        dialog.present()

    def on_rename_response(self, dialog, response, entry):
        if response != "rename" or not self.current:
            return
        name = entry.get_text().strip()
        if not name or "/" in name:
            return
        target = self.current.path.with_name(name)
        if target.exists():
            self.toast("Файл с таким именем уже существует")
            return
        try:
            self.current.path.rename(target)
            self.scan()
        except Exception as exc:
            self.toast(str(exc))

    def delete_current(self):
        if not self.current:
            return
        dialog = Adw.MessageDialog(transient_for=self, heading="Удалить файл?", body=self.current.path.name)
        dialog.add_response("cancel", "Отмена")
        dialog.add_response("delete", "Удалить")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self.on_delete_response)
        dialog.present()

    def on_delete_response(self, _dialog, response):
        if response != "delete" or not self.current:
            return
        worker = self.active.get(self.current.file_id)
        if worker:
            worker.cancel()
        try:
            self.current.path.unlink()
            self.player.stop()
            self.current = None
            self.scan()
        except Exception as exc:
            self.toast(str(exc))

    def toast(self, text):
        dialog = Adw.MessageDialog(transient_for=self, heading=text)
        dialog.add_response("ok", "ОК")
        dialog.present()

    def on_close_request(self, _window):
        self.flush_notes()
        for worker in self.active.values():
            worker.cancel()
        self.player.stop()
        return False


class TranscriberApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            .viewer { background: black; border-radius: 12px; }
            .toolbar-card { padding: 8px; border-radius: 12px; background: alpha(currentColor, .06); }
            .thumb { border-radius: 10px; background: alpha(currentColor, .07); }
            textview.card { padding: 8px; border-radius: 12px; }
            """
        )
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = MainWindow(self)
        win.present()


def main():
    app = TranscriberApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
