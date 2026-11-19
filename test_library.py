"""Verify reversible deletion, preservation of media, and active-job protection."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import server


class LibraryTests(unittest.TestCase):
    """Exercise trash and restore against a temporary library."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(server, "DATA", self.root)
        self.patch.start()
        self.song_id = "b" * 32
        self.folder = self.root / self.song_id
        self.folder.mkdir()
        self.item = {"id": self.song_id, "title": "Mi canción", "created": "2026", "status": "complete"}
        self.save()
        (self.folder / "audio.mp3").write_bytes(b"original audio")
        (self.folder / "video-horizontal.mp4").write_bytes(b"original video")
        self.client = TestClient(server.app)
        self.headers = {"x-studio": "ace-local"}

    def save(self):
        (self.folder / "song.json").write_text(json.dumps(self.item))

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_delete_and_restore_preserve_audio_and_video(self):
        result = self.client.delete(f"/api/songs/{self.song_id}", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get("/api/songs").json(), [])
        self.assertEqual(self.client.get(f"/api/songs/{self.song_id}/audio").status_code, 404)
        self.assertEqual(self.client.get("/api/trash").json()[0]["id"], self.song_id)
        result = self.client.post(f"/api/trash/{self.song_id}/restore", headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual((self.folder / "audio.mp3").read_bytes(), b"original audio")
        self.assertEqual((self.folder / "video-horizontal.mp4").read_bytes(), b"original video")
        self.assertEqual(self.client.get("/api/trash").json(), [])

    def test_reject_active_work_and_untrusted_requests(self):
        self.assertEqual(self.client.delete(f"/api/songs/{self.song_id}").status_code, 403)
        self.item["status"] = "generating"
        self.save()
        self.assertEqual(self.client.delete(f"/api/songs/{self.song_id}", headers=self.headers).status_code, 409)
        self.item["status"] = "complete"
        self.save()
        (self.folder / "video-horizontal.json").write_text(json.dumps({"status": "rendering"}))
        self.assertEqual(self.client.delete(f"/api/songs/{self.song_id}", headers=self.headers).status_code, 409)
        self.assertTrue((self.folder / "audio.mp3").exists())
        self.assertEqual(self.client.delete("/api/songs/bad-id", headers=self.headers).status_code, 404)


if __name__ == "__main__":
    unittest.main()
