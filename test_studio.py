"""Focused checks for song creation, failure recovery, and file boundaries."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import engine
import server


class StudioTests(unittest.TestCase):
    """Test the local studio without loading a music model."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = patch.object(server, "DATA", Path(self.temp.name))
        self.data.start()
        self.client = TestClient(server.app)
        self.song = {"title": "Prueba <segura>", "style": "banda", "lyrics": "[Chorus]\nMi canción"}

    def tearDown(self):
        self.data.stop()
        self.temp.cleanup()

    def test_generation_and_playback(self):
        def fake_generate(song, folder, progress):
            progress("Creando…")
            (folder / "audio.mp3").write_bytes(b"audio-test")
        finished = threading.Event()
        original_worker = server.worker
        def tracked_worker(*args):
            try:
                original_worker(*args)
            finally:
                finished.set()
        with patch.object(server, "generate", side_effect=fake_generate), patch.object(server, "worker", side_effect=tracked_worker):
            response = self.client.post("/api/songs", json=self.song, headers={"x-studio": "ace-local"})
            self.assertEqual(response.status_code, 202)
            item = response.json()
            self.assertTrue(finished.wait(5))
        self.assertFalse(server.lock.locked())
        self.assertEqual(self.client.get("/api/songs").json()[0]["status"], "complete")
        audio = self.client.get(f'/api/songs/{item["id"]}/audio')
        self.assertEqual(audio.content, b"audio-test")

    def test_failures_release_slot(self):
        folder = Path(self.temp.name) / "a"
        folder.mkdir()
        item = {"id": "a", "created": "2026", "status": "generating"}
        server.lock.acquire()
        with patch.object(server, "generate", side_effect=RuntimeError("Memoria insuficiente")):
            server.worker(folder, item)
        self.assertFalse(server.lock.locked())
        self.assertEqual(item["status"], "failed")
        self.assertEqual(item["message"], "Memoria insuficiente")

    def test_validation_and_single_generation(self):
        headers = {"x-studio": "ace-local"}
        self.assertEqual(self.client.post("/api/songs", json=self.song).status_code, 403)
        self.assertEqual(self.client.post("/api/songs", json={**self.song, "duration": 75}, headers=headers).status_code, 422)
        self.assertEqual(self.client.post("/api/songs", json={**self.song, "lyrics": ""}, headers=headers).status_code, 422)
        server.lock.acquire()
        try:
            self.assertEqual(self.client.post("/api/songs", json=self.song, headers=headers).status_code, 409)
        finally:
            server.lock.release()
        self.assertEqual(self.client.get("/api/songs/not-a-library-id/audio").status_code, 404)

    def test_instrumental_any_style_and_unknown_style(self):
        headers = {"x-studio": "ace-local"}
        with patch.object(server, "worker", side_effect=lambda folder, item: server.lock.release()):
            result = self.client.post("/api/songs", json={**self.song, "style": "bachata", "instrumental": True, "lyrics": ""}, headers=headers)
            self.assertEqual(result.status_code, 202)
            self.assertTrue(result.json()["instrumental"])
        self.assertEqual(self.client.post("/api/songs", json={**self.song, "style": "desconocido"}, headers=headers).status_code, 422)

    def test_seed_and_profile_validation_and_persistence(self):
        headers = {"x-studio": "ace-local"}
        for invalid in ({"seed": -1}, {"seed": 2**31}, {"quality": "unknown"}):
            self.assertEqual(self.client.post("/api/songs", json={**self.song, **invalid}, headers=headers).status_code, 422)
        with patch.object(server, "worker", side_effect=lambda folder, item: server.lock.release()):
            for options in ({}, {"seed": 0, "quality": "fast"}):
                response = self.client.post("/api/songs", json={**self.song, **options}, headers=headers)
                self.assertEqual(response.status_code, 202)
                item = response.json()
                self.assertIsInstance(item["seed"], int)
                self.assertGreaterEqual(item["seed"], 0)
                saved = __import__("json").loads((server.DATA / item["id"] / "song.json").read_text())
                self.assertEqual(saved["seed"], item["seed"])
                if options:
                    self.assertEqual(item["seed"], 0)
                    self.assertEqual(item["quality"], "fast")

    def test_genre_and_mood_reach_engine_without_default_acoustic_bias(self):
        source = Path(self.temp.name) / "result.mp3"
        source.write_bytes(b"audio")
        folder = Path(self.temp.name) / "song"
        folder.mkdir()
        captions = []
        with patch.object(engine, "Client") as client:
            client.return_value.view_api.return_value = {"named_endpoints": {"/generation_wrapper": {"parameters": []}}}
            client.return_value.predict.return_value = [str(source)]
            for style in ("banda", "cumbia", "balada"):
                song = {**self.song, "style": style, "description": "", "mood": "sincero", "instrumental": True, "voice": "male", "bpm": 100, "duration": 15}
                engine.generate(song, folder, lambda message: None)
                caption = client.return_value.predict.call_args.kwargs["param_0"]
                self.assertTrue(caption.startswith(engine.PRESETS[style]))
                self.assertNotIn("restrained arrangement", caption)
                self.assertNotIn("natural acoustic sound", caption)
                captions.append(caption)
        self.assertEqual(len(set(captions)), 3)

    def test_language_selection_defaults_validation_and_engine(self):
        headers = {"x-studio": "ace-local"}
        with patch.object(server, "worker", side_effect=lambda folder, item: server.lock.release()):
            for options, expected in (({}, "es"), ({"language": "en"}, "en")):
                r = self.client.post("/api/songs", json={**self.song, **options}, headers=headers)
                self.assertEqual(r.status_code, 202)
                self.assertEqual(r.json()["language"], expected)
        self.assertEqual(self.client.post("/api/songs", json={**self.song, "language": "fr"}, headers=headers).status_code, 422)
        source = Path(self.temp.name) / "english.mp3"
        source.write_bytes(b"audio")
        folder = Path(self.temp.name) / "english"
        folder.mkdir()
        song = {**self.song, "style": "pop", "language": "en", "lyrics": "[Chorus]\nOne more day", "description": "", "voice": "female", "bpm": 100, "duration": 15}
        with patch.object(engine, "Client") as client:
            client.return_value.view_api.return_value = {"named_endpoints": {"/generation_wrapper": {"parameters": []}}}
            client.return_value.predict.return_value = [str(source)]
            engine.generate(song, folder, lambda message: None)
            args = client.return_value.predict.call_args.kwargs
            self.assertEqual(args["param_5"], "en")
            self.assertIn("sung in English", args["param_0"])
            self.assertNotIn("Spanish language pop", args["param_0"])
            self.assertEqual(args["param_1"], song["lyrics"])
            engine.generate({**song, "instrumental": True}, folder, lambda message: None)
            args = client.return_value.predict.call_args.kwargs
            self.assertEqual(args["param_5"], "unknown")
            self.assertNotIn("sung in English", args["param_0"])

    def test_every_visible_style_exists_in_engine(self):
        import re
        catalog = (Path(server.ROOT) / "web/presets.js").read_text().split("const names=")[0]
        ids = set(re.findall(r"\['([^']+)','[^']+'\]", catalog))
        self.assertEqual(ids, set(engine.PRESETS))
        self.assertEqual(len(ids), 25)

    def test_engine_uses_download_files_when_audio_update_has_no_path(self):
        source = Path(self.temp.name) / "generated.mp3"
        source.write_bytes(b"mp3")
        target = Path(self.temp.name) / "song"
        target.mkdir()
        result = [{"__type__": "update"}] + [None] * 7 + [[str(source)]] + ["", "Generation Complete"]
        with patch.object(engine, "Client") as client:
            client.return_value.view_api.return_value = {"named_endpoints": {"/generation_wrapper": {"parameters": []}}}
            client.return_value.predict.return_value = result
            song = {**self.song, "voice": "female", "description": "", "bpm": 88, "duration": 15}
            output = engine.generate(song, target, lambda message: None)
            self.assertEqual(output.read_bytes(), b"mp3")
            kwargs = client.return_value.predict.call_args.kwargs
            self.assertEqual(kwargs["param_11"], 15)
            self.assertEqual(kwargs["param_12"], 1)
            self.assertEqual(kwargs["param_37"], "320k")
            self.assertEqual(kwargs["param_38"], 44100)
            self.assertIn("female", kwargs["param_0"])
            self.assertEqual(kwargs["param_6"], 8)
            engine.generate({**song, "seed": 0, "quality": "fast"}, target, lambda message: None)
            kwargs = client.return_value.predict.call_args.kwargs
            self.assertEqual(kwargs["param_6"], 4)
            self.assertEqual(kwargs["param_9"], "0")
            self.assertFalse(kwargs["param_8"])


if __name__ == "__main__":
    unittest.main()
