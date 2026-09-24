import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import summary


class SummaryTests(unittest.TestCase):
    def test_custom_key_overrides_default_and_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            default = root / "default_key"
            custom = root / "config" / "openai_api_key"
            default.write_text("default-key\n")
            with patch.object(summary, "DEFAULT_KEY_PATH", default), patch.object(summary, "CUSTOM_KEY_PATH", custom):
                self.assertEqual(summary.read_api_key(), "default-key")
                summary.save_api_key("  custom-key  ")
                self.assertEqual(summary.read_api_key(), "custom-key")
                self.assertEqual(custom.stat().st_mode & 0o777, 0o600)
                summary.clear_custom_key()
                self.assertEqual(summary.read_api_key(), "default-key")

    def test_response_request_sends_transcript_and_reads_sentence(self):
        result = {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": " Иван обсудил с Анной договор. "}]}],
        }
        with patch.object(summary, "read_api_key", return_value="test-key"):
            with patch.object(summary.request, "urlopen", return_value=io.BytesIO(json.dumps(result).encode())) as urlopen:
                self.assertEqual(summary.summarize_call("Тестовый разговор"), "Иван обсудил с Анной договор.")
        api_request = urlopen.call_args.args[0]
        payload = json.loads(api_request.data)
        self.assertEqual(payload["model"], "gpt-5-nano")
        self.assertEqual(payload["input"], "Тестовый разговор")
        self.assertIs(payload["store"], False)
        self.assertEqual(payload["reasoning"]["effort"], "minimal")


if __name__ == "__main__":
    unittest.main()
