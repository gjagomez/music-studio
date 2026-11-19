"""Check real video export and the local job API without generating music."""
import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import video_routes
from video_renderer import ffmpeg_binary, render_video


class VideoTests(unittest.TestCase):
    """Exercise both layouts, bad requests, and recovery after export failure."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.song_id = "a" * 32
        self.folder = self.data / self.song_id
        self.folder.mkdir()
        self.song = {"title": "Mi canción: 100% [fe] 'amor'"}
        (self.folder / "song.json").write_text(json.dumps(self.song))
        (self.folder / "audio.mp3").write_bytes(b"fake-audio")
        self.app = FastAPI()
        video_routes.register_video_routes(self.app, lambda: self.data)
        self.client = TestClient(self.app)

    def tearDown(self):
        self.temp.cleanup()

    def wait_job(self):
        for _ in range(100):
            result = self.client.get("/api/videos/status").json()
            if not result["busy"]:
                return result
            time.sleep(.01)
        self.fail("Video job did not finish")

    def test_api_success_and_validation(self):
        finished = threading.Event()
        def fake_render(folder, *args):
            (folder / "video-horizontal.mp4").write_bytes(b"video")
            finished.set()
        with patch.object(video_routes, "render_video", side_effect=fake_render), patch.object(video_routes, "ffmpeg_binary", return_value="ffmpeg"):
            response = self.client.post(f"/api/songs/{self.song_id}/video", json={"layout": "horizontal", "visualizer": "lightning", "wave_color": "#ff579b", "screen_text": "Una frase en pantalla", "text_color": "#36cfff", "text_position": "top", "text_size": "large"})
            self.assertEqual(response.status_code, 202)
            self.assertTrue(finished.wait(3))
            state = self.wait_job()
            self.assertEqual(state["items"][0]["status"], "complete")
            self.assertEqual(state["items"][0]["wave_color"], "#ff579b")
            self.assertEqual(state["items"][0]["screen_text"], "Una frase en pantalla")
            self.assertEqual(state["items"][0]["visualizer"], "lightning")
        self.assertEqual(self.client.get(f"/api/songs/{self.song_id}/video/horizontal").content, b"video")
        self.assertEqual(self.client.post(f"/api/songs/{self.song_id}/video", json={"layout": "square"}).status_code, 422)
        self.assertEqual(self.client.post("/api/songs/invalid/video", json={}).status_code, 404)

    def test_failure_releases_video_slot(self):
        with patch.object(video_routes, "render_video", side_effect=RuntimeError("Fallo de exportación")), patch.object(video_routes, "ffmpeg_binary", return_value="ffmpeg"):
            for _ in range(2):
                response = self.client.post(f"/api/songs/{self.song_id}/video", json={})
                self.assertEqual(response.status_code, 202)
                state = self.wait_job()
                self.assertEqual(state["items"][0]["status"], "failed")
                self.assertIn("Fallo", state["items"][0]["message"])

    def test_real_mp4_both_layouts(self):
        binary = ffmpeg_binary()
        probe = shutil.which("ffprobe") or str(Path(binary).with_name("ffprobe"))
        subprocess.run([binary, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(self.folder / "audio.mp3")], check=True)
        for layout, expected in [("horizontal", (1280, 720)), ("vertical", (720, 1280))]:
            output = render_video(self.folder, self.song, layout, "gold", "Mi canal cristiano", {"screen_text": "Mi mensaje\nCon acentos: canción", "text_color": "#36cfff", "text_position": "bottom", "text_size": "large", "overlay_text": False})
            from PIL import Image
            image = Image.open(self.folder / f"scene-{layout}-0.png").convert("RGB")
            pixels = image.crop((0, int(image.height*.75), image.width, image.height)).getdata()
            self.assertGreater(sum(1 for r,g,b in pixels if r < 80 and g > 180 and b > 180), 20)
            result = subprocess.run([probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)], capture_output=True, text=True, check=True)
            info = json.loads(result.stdout)
            video = next(s for s in info["streams"] if s["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"]), expected)
            self.assertEqual(video["codec_name"], "h264")
            self.assertTrue(any(s["codec_name"] == "aac" for s in info["streams"]))
            self.assertAlmostEqual(float(info["format"]["duration"]), 1, delta=.25)

        # Editing keeps the source intact and changes the exported duration.
        before = (self.folder / "audio.mp3").read_bytes()
        output = render_video(self.folder, self.song, "horizontal", "blue", "Nuevo canal", {
            "visualizer": "classic", "title": "Título editado", "start": .2, "end": .8, "volume": .5, "waveform": False,
        })
        result = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(output)], capture_output=True, text=True, check=True)
        self.assertAlmostEqual(float(json.loads(result.stdout)["format"]["duration"]), .6, delta=.15)
        self.assertEqual((self.folder / "audio.mp3").read_bytes(), before)

    def test_editor_rejects_invalid_trim_and_volume(self):
        endpoint = f"/api/songs/{self.song_id}/video"
        self.assertEqual(self.client.post(endpoint, json={"start": 20, "end": 10}).status_code, 422)
        self.assertEqual(self.client.post(endpoint, json={"volume": 10}).status_code, 422)
        for invalid in ({"screen_text": "x"*301}, {"text_position": "bad"}, {"text_size": "bad"}, {"text_color": "red"}, {"wave_color": "red;movie=x"}, {"wave_color": "#123"}, {"visualizer": "unknown"}):
            self.assertEqual(self.client.post(endpoint, json=invalid).status_code, 422)
        with patch.object(video_routes, "audio_duration", return_value=30):
            self.assertEqual(self.client.post(endpoint, json={"end": 31}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
