"""Input normalization, model readiness, job boundaries and preservation of source songs."""
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PIL import Image
from fastapi.testclient import TestClient
import server
import avatar_routes
from video_renderer import ffmpeg_binary


class AvatarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = patch.object(server, 'DATA', self.root)
        self.data.start()
        self.client = TestClient(server.app)
        self.headers = {'x-studio': 'ace-local'}
        self.song_id = 'c'*32
        self.folder = self.root/self.song_id
        self.folder.mkdir()
        (self.folder/'song.json').write_text(json.dumps({'id': self.song_id, 'title': 'Prueba', 'created': '2026', 'status': 'complete', 'style': 'pop', 'duration': 3}))
        subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', str(self.folder/'audio.mp3')], check=True)
        image = io.BytesIO()
        Image.new('RGB', (1000, 800), '#8055dd').save(image, format='PNG')
        r = self.client.post(f'/api/songs/{self.song_id}/avatar-inputs', files={'file': ('../foto.png', image.getvalue())}, headers=self.headers)
        self.assertEqual(r.status_code, 201, r.text)
        self.source = r.json()

    def tearDown(self):
        self.data.stop()
        self.temp.cleanup()

    def wait_idle(self):
        for _ in range(300):
            jobs = self.client.get(f'/api/songs/{self.song_id}/avatars').json()
            if jobs and not any(j['status'] == 'rendering' for j in jobs):
                return jobs
            time.sleep(.01)
        self.fail('La animación no terminó.')

    def test_upload_validation_and_readiness(self):
        self.assertEqual(self.source['name'], 'foto.png')
        response = self.client.get(self.source['url'])
        self.assertEqual(response.status_code, 200)
        with Image.open(io.BytesIO(response.content)) as image:
            self.assertLessEqual(max(image.size), 720)
        self.assertEqual(self.client.post(f'/api/songs/{self.song_id}/avatar-inputs', files={'file': ('a.png', b'not image')}, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post(f'/api/songs/{self.song_id}/avatar-inputs', files={'file': ('a.png', b'no header')}).status_code, 403)
        with patch.object(avatar_routes, 'engine_ready', return_value=False):
            self.assertFalse(self.client.get('/api/avatar/status').json()['ready'])
            self.assertEqual(self.client.post(f'/api/songs/{self.song_id}/avatars', json={'source_id': self.source['id']}, headers=self.headers).status_code, 503)
        self.assertEqual(self.client.get(f'/api/songs/{self.song_id}/avatar-inputs/not-an-id').status_code, 404)

    def test_job_protects_source_and_preserves_every_export(self):
        entered, proceed = threading.Event(), threading.Event()
        original = (self.folder/'audio.mp3').read_bytes()
        def fake(folder, source, audio, options, progress):
            entered.set()
            proceed.wait(5)
            progress('Probando sincronización', 60)
            (folder/'video.mp4').write_bytes(b'video-test')
        with patch.object(avatar_routes, 'engine_ready', return_value=True), patch.object(avatar_routes, 'run_avatar', side_effect=fake):
            r = self.client.post(f'/api/songs/{self.song_id}/avatars', json={'source_id': self.source['id'], 'start': 0, 'length': 5}, headers=self.headers)
            self.assertEqual(r.status_code, 202, r.text)
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.client.delete(f'/api/songs/{self.song_id}', headers=self.headers).status_code, 409)
            self.assertEqual(self.client.post('/api/backup/create', json={}, headers=self.headers).status_code, 409)
            self.assertTrue(server.lock.locked())
            proceed.set()
            jobs = self.wait_idle()
            self.assertEqual(jobs[0]['status'], 'complete')
            self.assertLessEqual(jobs[0]['duration'], 3.1)
            self.assertEqual(self.client.get(jobs[0]['url']).content, b'video-test')
            old = jobs[0]['id']
            self.assertEqual(self.client.post(f'/api/songs/{self.song_id}/avatars', json={'source_id': self.source['id'], 'length': 1}, headers=self.headers).status_code, 202)
            jobs = self.wait_idle()
            self.assertEqual(len(jobs), 2)
            self.assertIn(old, [j['id'] for j in jobs])
        self.assertEqual((self.folder/'audio.mp3').read_bytes(), original)
        self.assertFalse(server.lock.locked())

    def test_failure_releases_slots_and_bad_ranges(self):
        with patch.object(avatar_routes, 'engine_ready', return_value=True), patch.object(avatar_routes, 'run_avatar', side_effect=RuntimeError('No se detectó un rostro claro.')):
            endpoint = f'/api/songs/{self.song_id}/avatars'
            for invalid in ({'start': -1}, {'length': 31}, {'source_id': '../file'}, {'layout': 'square'}, {'start': 8}):
                self.assertEqual(self.client.post(endpoint, json={'source_id': self.source['id'], **invalid}, headers=self.headers).status_code, 422)
            for _ in range(2):
                self.assertEqual(self.client.post(endpoint, json={'source_id': self.source['id'], 'length': 1}, headers=self.headers).status_code, 202)
                self.assertTrue(all(j['status'] == 'failed' for j in self.wait_idle()))
            self.assertFalse(server.lock.locked())
            self.assertFalse(self.client.get('/api/avatar/status').json()['busy'])

    def test_real_video_upload_is_silent_and_bounded(self):
        path = self.root/'input.mp4'
        subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True)
        r = self.client.post(f'/api/songs/{self.song_id}/avatar-inputs', files={'file': ('clip.mp4', path.read_bytes())}, headers=self.headers)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()['kind'], 'video')
        self.assertEqual(self.client.get(r.json()['url']).headers['content-type'], 'video/mp4')
