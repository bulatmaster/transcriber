import tempfile
import unittest
import wave
from pathlib import Path

import app


def write_wav(path, channels=1, rate=16000, frames=b"\x00\x00\xff\x7f\x00\x80"):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(channels)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(frames)


class LoadWavTests(unittest.TestCase):
    def test_reads_mono_16k_as_float_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"
            write_wav(path)
            samples = app.load_wav(path)
            self.assertEqual(samples.dtype.name, "float32")
            self.assertEqual(samples.tolist(), [0.0, 32767 / 32768, -1.0])

    def test_rejects_unexpected_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audio.wav"
            write_wav(path, rate=44100)
            with self.assertRaises(RuntimeError):
                app.load_wav(path)


if __name__ == "__main__":
    unittest.main()
