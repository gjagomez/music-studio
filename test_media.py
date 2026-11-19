"""Check uploads, scene selection boundaries, and real image-sequence export."""
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import server
from video_renderer import ffmpeg_binary, render_video


class MediaTests(unittest.TestCase):
    """Keep image handling and exports isolated from the user's library."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(server, "DATA", self.root)
        self.patch.start()
        self.song_id = "c" * 32
        self.folder = self.root / self.song_id
        self.folder.mkdir()
        (self.folder / "song.json").write_text(json.dumps({"title": "Escenas", "status": "complete"}))
        (self.folder / "audio.mp3").write_bytes(b"fake")
        self.client = TestClient(server.app)
        self.headers = {"x-studio": "ace-local"}

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def upload(self, color):
        buffer = io.BytesIO()
        Image.new("RGB", (160, 90), color).save(buffer, format="PNG")
        return self.client.post(f"/api/songs/{self.song_id}/images", files={"file": ("image.png", buffer.getvalue(), "image/png")}, headers=self.headers)

    def test_upload_and_invalid_files(self):
        response = self.upload("red")
        self.assertEqual(response.status_code, 201)
        item = response.json()
        self.assertEqual(self.client.get(item["url"]).headers["content-type"], "image/jpeg")
        self.assertEqual(len(self.client.get(f"/api/songs/{self.song_id}/images").json()), 1)
        bad = self.client.post(f"/api/songs/{self.song_id}/images", files={"file": ("fake.png", b"not an image", "image/png")}, headers=self.headers)
        self.assertEqual(bad.status_code, 422)
        endpoint = f"/api/songs/{self.song_id}/video"
        self.assertEqual(self.client.post(endpoint, json={"images": ["../../secret"]}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post(endpoint, json={"images": ["a"*32]}, headers=self.headers).status_code, 422)

    def test_real_slideshow_uses_images_in_order(self):
        ids = [self.upload(color).json()["id"] for color in ["red", "blue"]]
        binary = ffmpeg_binary()
        subprocess.run([binary, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", str(self.folder / "audio.mp3")], check=True)
        output = render_video(self.folder, {"title": "Escenas"}, "horizontal", "gold", "", {"images": ids, "motion": True, "overlay_text": False, "waveform": False})
        for second, expected in [(0.3, 0), (1.4, 2)]:
            frame = self.folder / f"frame-{second}.png"
            subprocess.run([binary, "-loglevel", "error", "-y", "-ss", str(second), "-i", str(output), "-frames:v", "1", str(frame)], check=True)
            with Image.open(frame) as image:
                pixel = image.convert("RGB").getpixel((640, 360))
                self.assertGreater(pixel[expected], 180)
                self.assertLess(pixel[2 if expected == 0 else 0], 60)


if __name__ == "__main__":
    unittest.main()
