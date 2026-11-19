"""Bounded local melody uploads, normalized before passing to the music engine."""
import subprocess
import tempfile
import uuid
import wave
from pathlib import Path

from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from library_routes import validate_id
from video_renderer import ffmpeg_binary


def register_melody_routes(app, data):
    @app.post('/api/melodies', status_code=201)
    def upload_melody(file: UploadFile = File(...)):
        try:
            content = file.file.read(20 * 1024 * 1024 + 1)
        finally:
            file.file.close()
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(413, 'Usá un audio de hasta 20 MB.')
        root = data() / '_melodies'
        root.mkdir(exist_ok=True)
        melody_id = uuid.uuid4().hex
        output = root / (melody_id + '.wav')
        try:
            with tempfile.TemporaryDirectory(dir=root) as temp:
                source = Path(temp) / 'input'
                source.write_bytes(content)
                subprocess.run([ffmpeg_binary(), '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(source), '-t', '30.1', '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '32000', '-c:a', 'pcm_s16le', str(output)], check=True, capture_output=True, timeout=30)
            with wave.open(str(output)) as audio:
                duration = audio.getnframes() / audio.getframerate()
            if not 1 <= duration <= 30:
                raise ValueError()
        except (OSError, ValueError, wave.Error, subprocess.SubprocessError):
            output.unlink(missing_ok=True)
            raise HTTPException(422, 'Subí una melodía válida de 1 a 30 segundos (WAV, MP3, M4A, OGG o WebM).')
        return {'id': melody_id, 'duration': round(duration, 2), 'url': f'/api/melodies/{melody_id}'}

    @app.get('/api/melodies/{melody_id}')
    def melody_audio(melody_id: str):
        validate_id(melody_id)
        path = data() / '_melodies' / (melody_id + '.wav')
        if not path.is_file():
            raise HTTPException(404, 'Melodía no encontrada.')
        return FileResponse(path, media_type='audio/wav')
