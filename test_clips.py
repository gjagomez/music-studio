"""Validate imported scenes and their use as a looping music-video background."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from video_renderer import ffmpeg_binary, render_video


class ClipTests(unittest.TestCase):
    """Export real small clips while isolating all user files."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(server, "DATA", self.root)
        self.patch.start()
        self.folder = self.root / ("d" * 32)
        self.folder.mkdir()
        (self.folder / "song.json").write_text(json.dumps({"title": "Escena", "status": "complete"}))
        self.client = TestClient(server.app)
        self.endpoint = f"/api/songs/{self.folder.name}/clips"
        self.headers = {"x-studio": "ace-local"}

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_import_and_loop_clip_with_song_audio(self):
        binary = ffmpeg_binary()
        source = self.root / "scene.mp4"
        subprocess.run([binary, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x192:d=0.5", "-c:v", "libx264", "-threads", "2", str(source)], check=True)
        result = self.client.post(self.endpoint, files={"file": ("Escena Wan.mp4", source.read_bytes(), "video/mp4")}, headers=self.headers)
        self.assertEqual(result.status_code, 201, result.text)
        clip = result.json()
        self.assertEqual(len(self.client.get(self.endpoint).json()), 1)
        self.assertEqual(self.client.get(clip["url"]).status_code, 200)
        subprocess.run([binary, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(self.folder / "audio.mp3")], check=True)
        output = render_video(self.folder, {"title": "Escena con música"}, "horizontal", "gold", "Mi canal", {"clip": clip["id"], "waveform": True})
        probe = str(Path(binary).with_name("ffprobe"))
        info = subprocess.run([probe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)], capture_output=True, text=True, check=True)
        info = json.loads(info.stdout)
        self.assertAlmostEqual(float(info["format"]["duration"]), 2, delta=.2)
        self.assertTrue(any(s["codec_type"] == "audio" for s in info["streams"]))

    def test_invalid_video_and_unknown_clip_are_rejected(self):
        result = self.client.post(self.endpoint, files={"file": ("fake.mp4", b"invalid", "video/mp4")}, headers=self.headers)
        self.assertEqual(result.status_code, 422)
        (self.folder / "audio.mp3").write_bytes(b"audio")
        self.assertEqual(self.client.post(f"/api/songs/{self.folder.name}/video", json={"clip": "a" * 32}, headers=self.headers).status_code, 422)


if __name__ == "__main__":
    unittest.main()
