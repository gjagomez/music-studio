import io
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
import engine
import server


def wav(seconds):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, 8000, 0, 'NONE', 'NONE'))
        audio.writeframes(b'\0\0' * int(seconds * 8000))
    return buffer.getvalue()


class MelodyTests(unittest.TestCase):
    def test_upload_cover_and_validation(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, 'DATA', Path(temp)):
            root = Path(temp)
            client = TestClient(server.app)
            headers = {'x-studio': 'ace-local'}
            for blob in (b'bad audio', wav(.2), wav(31)):
                self.assertEqual(client.post('/api/melodies', files={'file': ('audio.wav', blob)}, headers=headers).status_code, 422)
            self.assertEqual(list((root / '_melodies').glob('*.wav')), [])
            self.assertEqual(client.post('/api/melodies', files={'file': ('audio.wav', wav(2))}).status_code, 403)
            r = client.post('/api/melodies', files={'file': ('../audio.wav', wav(2))}, headers=headers)
            self.assertEqual(r.status_code, 201, r.text)
            melody = r.json()
            self.assertEqual(melody['duration'], 2)
            self.assertEqual(client.get(melody['url']).status_code, 200)
            song = {'title': 'Guía', 'style': 'banda', 'instrumental': True}
            for extra in ({'melody_id': '../x'}, {'melody_id': 'a'*32}, {'melody_strength': 1.1}):
                self.assertEqual(client.post('/api/songs', json={**song, **extra}, headers=headers).status_code, 422)
            with patch.object(server, 'worker', side_effect=lambda folder, item: server.lock.release()):
                r = client.post('/api/songs', json={**song, 'melody_id': melody['id'], 'melody_strength': .85}, headers=headers)
            self.assertEqual(r.status_code, 202)
            item = r.json()
            output = root / 'result.mp3'
            output.write_bytes(b'audio')
            with patch.object(engine, 'Client') as mock, patch.object(engine, 'handle_file', side_effect=lambda path: {'path': path}):
                mock.return_value.view_api.return_value = {'named_endpoints': {'/_handle_mode_change': {}, '/generation_wrapper': {'parameters': []}}}
                mock.return_value.predict.return_value = [str(output)]
                engine.generate(item, root / item['id'], lambda message: None)
                args = mock.return_value.predict.call_args.kwargs
                self.assertNotIn('param_20', args)
                self.assertEqual(mock.return_value.predict.call_args_list[0].kwargs, {'mode': 'Remix', 'api_name': '/_handle_mode_change'})
                self.assertEqual(args['param_18'], 1.0)
                self.assertEqual(args['param_19'], .85)
                self.assertEqual(args['param_11'], 2.0)
                self.assertTrue(Path(args['param_13']['path']).is_file())
                self.assertIsNone(args['param_10'])
