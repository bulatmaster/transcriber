import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class StoreTests(unittest.TestCase):
    def test_old_database_migrates_and_summary_preserves_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "transcriber.sqlite3"
            conn = sqlite3.connect(db)
            conn.execute(
                "create table files(id text primary key, last_path text, kind text, duration real, "
                "transcript text, notes text, updated_at real)"
            )
            conn.execute("insert into files(id, notes) values('recording', 'Моя заметка')")
            conn.commit()
            conn.close()
            with patch.object(app, "APP_DIR", root), patch.object(app, "DB_PATH", db):
                with patch.object(app, "AUDIO_DIR", root / "audio"), patch.object(app, "THUMB_DIR", root / "thumbs"):
                    store = app.Store()
                    self.assertIn("summary", {row[1] for row in store.conn.execute("pragma table_info(files)")})
                    self.assertEqual(store.set_summary("recording", "Первое саммари."), "Саммари: Первое саммари.\n\nМоя заметка")
                    self.assertEqual(store.set_summary("recording", "Новое саммари."), "Саммари: Новое саммари.\n\nМоя заметка")
                    store.set_notes("recording", "")
                    self.assertEqual(store.set_summary("recording", "Ещё одно саммари."), "Саммари: Ещё одно саммари.")
                    self.assertEqual(store.set_summary("recording", "Итоговое саммари."), "Саммари: Итоговое саммари.")
                    store.conn.close()


if __name__ == "__main__":
    unittest.main()
