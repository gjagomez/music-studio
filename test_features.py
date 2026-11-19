"""Real audio edits, sequential generation, portable restore and timed lyric exports."""
import io
import json
import subprocess
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageChops
import server
import studio_features
from lyrics_timing import parse_lrc
from video_renderer import audio_duration, ffmpeg_binary, render_video


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'library'
        self.root.mkdir()
        self.data = patch.object(server, 'DATA', self.root)
        self.data.start()
        self.client = TestClient(server.app)
        self.headers = {'x-studio': 'ace-local'}
        self.song_id = 'e'*32
        self.folder = self.root/self.song_id
        self.folder.mkdir()
        self.song = {'id': self.song_id, 'title': 'Original', 'style': 'reggaeton', 'voice': 'female',
                     'lyrics': '[Chorus]\nUna frase\nOtra frase', 'description': '', 'duration': 15,
                     'bpm': 100, 'created': '2026-11-14', 'status': 'complete'}
        (self.folder/'song.json').write_text(json.dumps(self.song))
        subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3', str(self.folder/'audio.mp3')], check=True)

    def tearDown(self):
        self.data.stop()
        self.temp.cleanup()

    def wait_idle(self):
        for _ in range(300):
            songs = self.client.get('/api/songs').json()
            if not any(s['status'] in {'generating', 'queued'} for s in songs):
                return songs
            time.sleep(.01)
        self.fail('El trabajo no terminó.')

    def test_organization_preserves_generation_metadata(self):
        before = (self.folder/'song.json').read_bytes()
        value = {'favorite': True, 'collection': 'Para publicar', 'notes': 'Cambiar el coro'}
        r = self.client.post(f'/api/songs/{self.song_id}/organization', json=value, headers=self.headers)
        self.assertEqual(r.status_code, 200)
        listed = self.client.get('/api/songs').json()[0]
        self.assertTrue(listed['favorite'])
        self.assertEqual(listed['collection'], 'Para publicar')
        self.assertEqual((self.folder/'song.json').read_bytes(), before)
        self.assertEqual(self.client.post(f'/api/songs/{self.song_id}/organization', json=value).status_code, 403)

    def test_real_edit_preserves_original_and_protects_active_source(self):
        self.client.post(f'/api/songs/{self.song_id}/organization', json={'collection': 'Recortes'}, headers=self.headers)
        original = (self.folder/'audio.mp3').read_bytes()
        entered, proceed = threading.Event(), threading.Event()
        run = subprocess.run
        def controlled(args, **kwargs):
            if any('edited.partial.mp3' in str(a) for a in args):
                entered.set()
                proceed.wait(5)
            return run(args, **kwargs)
        with patch.object(studio_features.subprocess, 'run', side_effect=controlled):
            r = self.client.post(f'/api/songs/{self.song_id}/audio-edits', json={'title': 'Recortada', 'start': .5, 'end': 2.5, 'volume': .5, 'fade_in': .2, 'fade_out': .4}, headers=self.headers)
            self.assertEqual(r.status_code, 202, r.text)
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.client.delete(f'/api/songs/{self.song_id}', headers=self.headers).status_code, 409)
            proceed.set()
            songs = self.wait_idle()
        edited = next(s for s in songs if s['id'] != self.song_id)
        self.assertEqual(edited['status'], 'complete')
        self.assertEqual(edited['parent_id'], self.song_id)
        self.assertEqual(edited['collection'], 'Recortes')
        self.assertAlmostEqual(audio_duration(self.root/edited['id']/'audio.mp3'), 2, delta=.1)
        self.assertEqual((self.folder/'audio.mp3').read_bytes(), original)
        for value in ({'start': 2, 'end': 1}, {'start': 0, 'end': 30}, {'start': 0, 'end': 1, 'fade_in': 1, 'fade_out': 1}):
            self.assertEqual(self.client.post(f'/api/songs/{self.song_id}/audio-edits', json={'title': 'Error', **value}, headers=self.headers).status_code, 422)

    def test_variants_are_serial_and_keep_the_queue_locked(self):
        self.client.post(f'/api/songs/{self.song_id}/organization', json={'collection': 'Alternativas'}, headers=self.headers)
        self.song['language'] = 'en'
        (self.folder/'song.json').write_text(json.dumps(self.song))
        entered, proceed = threading.Event(), threading.Event()
        seeds = []
        active, maximum = 0, 0
        def fake_generate(song, folder, progress):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            seeds.append(song['seed'])
            entered.set()
            proceed.wait(5)
            progress('Probando versión')
            (folder/'audio.mp3').write_bytes(b'audio')
            active -= 1
        with patch.object(server, 'generate', side_effect=fake_generate):
            r = self.client.post(f'/api/songs/{self.song_id}/variants', json={'count': 3}, headers=self.headers)
            self.assertEqual(r.status_code, 202, r.text)
            self.assertTrue(entered.wait(3))
            pending = next(i for i in r.json()['items'] if i['status'] == 'queued')
            self.assertEqual(self.client.delete('/api/songs/'+pending['id'], headers=self.headers).status_code, 409)
            self.assertEqual(self.client.post('/api/songs', json={k:v for k,v in self.song.items() if k not in {'id','created','status'}}, headers=self.headers).status_code, 409)
            proceed.set()
            songs = self.wait_idle()
        self.assertTrue(all(s.get('collection') == 'Alternativas' for s in songs if s.get('parent_id') == self.song_id))
        self.assertTrue(all(s.get('language') == 'en' for s in songs if s.get('parent_id') == self.song_id))
        self.assertEqual(maximum, 1)
        self.assertEqual(len(set(seeds)), 3)
        self.assertEqual(len([s for s in songs if s.get('parent_id') == self.song_id]), 3)
        self.assertFalse(server.lock.locked())
        self.assertEqual(self.client.post(f'/api/songs/{self.song_id}/variants', json={'count': 9}, headers=self.headers).status_code, 422)

    def test_failed_variant_does_not_leave_the_queue_locked(self):
        count = 0
        def fake_generate(song, folder, progress):
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError('Prueba de fallo')
            (folder/'audio.mp3').write_bytes(b'audio')
        with patch.object(server, 'generate', side_effect=fake_generate):
            self.assertEqual(self.client.post(f'/api/songs/{self.song_id}/variants', json={'count': 2}, headers=self.headers).status_code, 202)
            songs = self.wait_idle()
        self.assertEqual(sorted(s['status'] for s in songs if s.get('parent_id')), ['complete', 'failed'])
        self.assertFalse(server.lock.locked())

    def test_backup_roundtrip_and_no_overwrite(self):
        self.client.post(f'/api/songs/{self.song_id}/organization', json={'favorite': True, 'collection': 'Favoritas', 'notes': 'Mi nota'}, headers=self.headers)
        r = self.client.post('/api/backup/create', json={}, headers=self.headers)
        self.assertEqual(r.status_code, 200, r.text)
        backup = self.client.get(r.json()['url']).content
        with zipfile.ZipFile(io.BytesIO(backup)) as archive:
            self.assertIn(self.song_id+'/audio.mp3', archive.namelist())
            self.assertIn(self.song_id+'/organization.json', archive.namelist())
        restored_root = Path(self.temp.name)/'restored'
        restored_root.mkdir()
        with patch.object(server, 'DATA', restored_root):
            for expected in ({'restored': 1, 'skipped': 0}, {'restored': 0, 'skipped': 1}):
                r = self.client.post('/api/backup/restore', files={'file': ('backup.zip', backup)}, headers=self.headers)
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json(), expected)
            item = self.client.get('/api/songs').json()[0]
            self.assertEqual(item['notes'], 'Mi nota')
            self.assertTrue(item['favorite'])
            self.assertEqual((restored_root/self.song_id/'audio.mp3').read_bytes(), (self.folder/'audio.mp3').read_bytes())
        malicious = io.BytesIO()
        with zipfile.ZipFile(malicious, 'w') as archive:
            archive.writestr('manifest.json', '{"format":"alma-backup-v1"}')
            archive.writestr('../outside.txt', 'bad')
        self.assertEqual(self.client.post('/api/backup/restore', files={'file': ('bad.zip', malicious.getvalue())}, headers=self.headers).status_code, 422)
        self.assertFalse((self.root.parent/'outside.txt').exists())

    def test_real_timed_lyrics_export(self):
        cues = '[00:00.00]Primera frase\n[00:00.80]Ahora cambia la letra'
        output = render_video(self.folder, self.song, 'horizontal', 'blue', '', {'start': 0, 'end': 1.6, 'waveform': False, 'overlay_text': False, 'lyrics_lrc': cues, 'text_color': '#ffffff'})
        self.assertAlmostEqual(audio_duration(output), 1.6, delta=.15)
        images = []
        for i, position in enumerate([.2, 1.1]):
            image = self.folder/f'check-{i}.png'
            subprocess.run([ffmpeg_binary(), '-v', 'error', '-y', '-ss', str(position), '-i', str(output), '-frames:v', '1', str(image)], check=True)
            images.append(Image.open(image).convert('RGB').crop((0, 600, 1280, 720)))
        self.assertIsNotNone(ImageChops.difference(*images).getbbox())
        self.assertEqual(len(parse_lrc(cues)), 2)
        for bad in ('sin tiempos', '[00:02]Una\n[00:01]Otra', '[00:02]Una\n[00:02]Otra'):
            with self.assertRaises(ValueError):
                parse_lrc(bad)
