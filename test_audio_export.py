"""Verify real MP3 downloads and preserve playback originals."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
from audio_export import download_mp3


class AudioExportTests(unittest.TestCase):
    """Exercise conversion, reuse, and the download HTTP response."""

    def test_download_is_320k_44100_and_playback_keeps_original(self):
        """A saved 48 kHz song downloads correctly without replacing its source."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            song_id = 'a' * 32
            song = root / song_id
            song.mkdir()
            source = song / 'audio.mp3'
            subprocess.run(['/opt/homebrew/bin/ffmpeg', '-v', 'error', '-f', 'lavfi',
                            '-i', 'sine=frequency=440:sample_rate=48000', '-t', '0.2',
                            '-c:a', 'libmp3lame', '-b:a', '128k', str(source)], check=True)
            original = source.read_bytes()
            with patch.object(server, 'DATA', root):
                client = TestClient(server.app)
                response = client.get(f'/api/songs/{song_id}/audio?download=true')
                self.assertEqual(response.status_code, 200)
                self.assertIn('attachment', response.headers['content-disposition'])
                self.assertEqual(client.get(f'/api/songs/{song_id}/audio').content, original)
            target = download_mp3(source)
            self.assertEqual(response.content, target.read_bytes())
            probe = subprocess.check_output(['/opt/homebrew/bin/ffprobe', '-v', 'error',
                '-show_entries', 'stream=sample_rate,bit_rate', '-of', 'json', str(target)])
            stream = json.loads(probe)['streams'][0]
            self.assertEqual(stream['sample_rate'], '44100')
            self.assertEqual(stream['bit_rate'], '320000')
            self.assertEqual(download_mp3(target), target)
            modified = target.stat().st_mtime_ns
            self.assertEqual(download_mp3(source).stat().st_mtime_ns, modified)
