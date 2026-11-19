"""Local face-video lip synchronization, isolated from music models and existing exports."""
import atexit
import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import uuid
import warnings
from datetime import datetime, timezone
from typing import Literal

from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, Field

from library_routes import LIBRARY_LOCK, validate_id
from studio_features import atomic_json
from video_renderer import audio_duration, ffmpeg_binary, SIZES

ENGINE = Path.home()/'Alma-Avatar'
RUNNER = Path(__file__).resolve().parent/'tools/avatar_infer.py'
REQUIRED = ['musetalkV15/unet.pth', 'musetalkV15/musetalk.json', 'sd-vae/config.json',
            'sd-vae/diffusion_pytorch_model.bin', 'whisper/pytorch_model.bin',
            'whisper/config.json', 'whisper/preprocessor_config.json',
            'face-parse-bisent/79999_iter.pth', 'face-parse-bisent/resnet18-5c106cde.pth']


class AvatarRequest(BaseModel):
    source_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    start: float = Field(default=0, ge=0, le=600)
    length: float = Field(default=5, ge=1, le=30)
    layout: Literal['horizontal', 'vertical'] = 'vertical'
    device: Literal['mps', 'cpu'] = 'mps'


def engine_ready():
    models = ENGINE/'upstream/models'
    return (ENGINE/'.venv/bin/python').is_file() and (ENGINE/'upstream/musetalk/models/vae.py').is_file() and (ENGINE/'upstream/musetalk/models/unet.py').is_file() and all((models/name).is_file() and (models/name).stat().st_size > 100 for name in REQUIRED)


def run_avatar(folder, source, song_audio, options, progress):
    """Use the song as driving audio, then mux the exact original musical excerpt."""
    binary = ffmpeg_binary()
    driven = folder/'voice.wav'
    subprocess.run([binary, '-nostdin', '-v', 'error', '-y', '-ss', str(options['start']), '-i', str(song_audio),
                    '-t', str(options['duration']), '-ac', '1', '-ar', '16000', str(driven)], check=True, capture_output=True, timeout=30)
    silent = folder/'animation.mp4'
    env = os.environ.copy()
    env.update(PYTORCH_ENABLE_MPS_FALLBACK='1', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               PYTHONUNBUFFERED='1', TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='4', GLOG_minloglevel='2')
    env['PATH'] = str(ENGINE/'.venv/bin')+os.pathsep+str(Path(binary).parent)+os.pathsep+env.get('PATH', '')
    command = [str(ENGINE/'.venv/bin/python'), str(RUNNER), '--engine', str(ENGINE/'upstream'),
               '--source', str(source), '--audio', str(driven), '--output', str(silent), '--device', options['device']]
    error = []
    with (folder/'inference.log').open('w') as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   errors='replace', env=env, cwd=ENGINE/'upstream', start_new_session=True)
        def cleanup():
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        atexit.register(cleanup)
        def read_output():
            for line in process.stdout:
                log.write(line)
                log.flush()
                if line.startswith('ALMA_PROGRESS '):
                    try:
                        update = json.loads(line[len('ALMA_PROGRESS '):])
                        progress(update['message'], max(0, min(95, int(update['percent']))))
                    except (ValueError, KeyError):
                        pass
                elif line.startswith('ALMA_ERROR '):
                    error.append(line[len('ALMA_ERROR '):].strip())
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            code = process.wait(timeout=2700)
        except subprocess.TimeoutExpired:
            cleanup()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise RuntimeError('El motor tardó demasiado. Probá con 5 segundos y una foto.')
        finally:
            reader.join(timeout=5)
            process.stdout.close()
            atexit.unregister(cleanup)
        if code or not silent.is_file():
            if error and error[-1].startswith(('No se detect', 'El rostro', 'No se pudo leer')):
                raise RuntimeError(error[-1])
            raise RuntimeError('No se pudo animar el rostro. Probá otra foto de frente, un fragmento de 5 segundos o el modo de compatibilidad.')
    progress('Uniendo la animación con tu canción…', 96)
    width, height = SIZES[options['layout']]
    partial = folder/'video.partial.mp4'
    result = subprocess.run([binary, '-nostdin', '-v', 'error', '-y', '-i', str(silent), '-ss', str(options['start']),
                             '-t', str(options['duration']), '-i', str(song_audio), '-map', '0:v:0', '-map', '1:a:0',
                             '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1',
                             '-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
                             '-c:a', 'aac', '-b:a', '192k', '-t', str(options['duration']), '-shortest', '-movflags', '+faststart', str(partial)],
                            capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError('La animación terminó, pero no se pudo guardar el MP4 final.')
    partial.replace(folder/'video.mp4')
    return folder/'video.mp4'


def register_avatar_routes(app, data, music_lock):
    slot = threading.Lock()
    app.state.avatar_busy = slot.locked
    for path in data().glob('*/avatars/*/job.json'):
        try:
            item = json.loads(path.read_text())
            if item.get('status') == 'rendering':
                atomic_json(path, {**item, 'status': 'failed', 'message': 'La animación se interrumpió. Podés crear otra sin perder tus videos anteriores.'})
        except (OSError, ValueError):
            pass

    def song_folder(song_id):
        validate_id(song_id)
        folder = data()/song_id
        if not (folder/'song.json').is_file() or not (folder/'audio.mp3').is_file():
            raise HTTPException(404, 'La canción todavía no tiene audio.')
        return folder

    @app.get('/api/avatar/status')
    def status():
        return {'ready': engine_ready(), 'busy': slot.locked(), 'music_busy': music_lock.locked(), 'max_seconds': 30,
                'engine': 'MuseTalk · local', 'message': 'Motor local listo' if engine_ready() else 'Falta instalar el motor local de sincronización de labios.'}

    @app.get('/api/songs/{song_id}/avatar-inputs')
    def inputs(song_id: str):
        with LIBRARY_LOCK:
            folder = song_folder(song_id)/'avatar-inputs'
            return [json.loads(p.read_text()) for p in sorted(folder.glob('*.json'))]

    @app.post('/api/songs/{song_id}/avatar-inputs', status_code=201)
    def upload(song_id: str, file: UploadFile = File(...)):
        try:
            content = file.file.read(100*1024*1024+1)
        finally:
            file.file.close()
        if len(content) > 100*1024*1024:
            raise HTTPException(413, 'El archivo puede pesar hasta 100 MB.')
        with LIBRARY_LOCK:
            root = song_folder(song_id)/'avatar-inputs'
            root.mkdir(exist_ok=True)
            if len(list(root.glob('*.json'))) >= 20:
                raise HTTPException(409, 'Esta canción ya tiene 20 fotos o videos guardados.')
            source_id = uuid.uuid4().hex
            image = None
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', Image.DecompressionBombWarning)
                    loaded = Image.open(io.BytesIO(content))
                    if loaded.format not in {'JPEG', 'PNG', 'WEBP'} or loaded.width*loaded.height > 24_000_000:
                        raise ValueError()
                    image = ImageOps.exif_transpose(loaded).convert('RGB')
                    image.thumbnail((720, 720))
            except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
                image = None
            if image is not None:
                path = root/(source_id+'.jpg')
                image.save(path, quality=94)
                kind, suffix = 'image', '.jpg'
            else:
                source, path = root/(source_id+'.upload'), root/(source_id+'.mp4')
                try:
                    source.write_bytes(content)
                    binary = ffmpeg_binary()
                    probe = str(Path(binary).with_name('ffprobe'))
                    result = subprocess.run([probe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(source)], capture_output=True, text=True, check=True, timeout=15)
                    info = json.loads(result.stdout)
                    stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
                    duration = float(info['format']['duration'])
                    if not math.isfinite(duration) or not .2 <= duration <= 30 or max(stream['width'], stream['height']) > 4096:
                        raise ValueError()
                    subprocess.run([binary, '-nostdin', '-v', 'error', '-y', '-i', str(source), '-t', '5', '-an',
                                    '-vf', 'scale=720:720:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=15',
                                    '-c:v', 'libx264', '-preset', 'veryfast', '-threads', '2', '-pix_fmt', 'yuv420p', str(path)],
                                   capture_output=True, check=True, timeout=60)
                    kind, suffix = 'video', '.mp4'
                except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError):
                    path.unlink(missing_ok=True)
                    raise HTTPException(422, 'Subí una foto JPG/PNG/WebP válida o un video MP4/MOV/WebM de hasta 30 segundos, con un rostro visible.')
                finally:
                    source.unlink(missing_ok=True)
            item = {'id': source_id, 'kind': kind, 'suffix': suffix, 'name': Path(file.filename or 'Mi personaje').name[:100],
                    'url': f'/api/songs/{song_id}/avatar-inputs/{source_id}'}
            atomic_json(root/(source_id+'.json'), item)
            return item

    @app.get('/api/songs/{song_id}/avatar-inputs/{source_id}')
    def source_file(song_id: str, source_id: str):
        validate_id(source_id)
        folder = song_folder(song_id)/'avatar-inputs'
        for suffix, media in [('.jpg', 'image/jpeg'), ('.mp4', 'video/mp4')]:
            path = folder/(source_id+suffix)
            if path.is_file():
                return FileResponse(path, media_type=media)
        raise HTTPException(404)

    @app.get('/api/songs/{song_id}/avatars')
    def jobs(song_id: str):
        folder = song_folder(song_id)
        items = []
        for path in (folder/'avatars').glob('*/job.json'):
            try:
                items.append(json.loads(path.read_text()))
            except (OSError, ValueError):
                pass
        return sorted(items, key=lambda v:v['created'], reverse=True)

    @app.post('/api/songs/{song_id}/avatars', status_code=202)
    def create(song_id: str, request: AvatarRequest):
        with LIBRARY_LOCK:
            folder = song_folder(song_id)
            if not engine_ready():
                raise HTTPException(503, 'El motor de sincronización de labios no está instalado todavía.')
            metadata = folder/'avatar-inputs'/(request.source_id+'.json')
            if not metadata.is_file():
                raise HTTPException(422, 'Subí o elegí primero la foto o el video del personaje.')
            source = json.loads(metadata.read_text())
            if source.get('suffix') not in {'.jpg', '.mp4'}:
                raise HTTPException(422, 'La foto o el video no es válido.')
            source_path = metadata.with_suffix(source['suffix'])
            if not source_path.is_file():
                raise HTTPException(422, 'Volvé a subir el personaje.')
            duration = min(request.length, audio_duration(folder/'audio.mp3')-request.start)
            if duration < 1:
                raise HTTPException(422, 'Elegí un inicio que deje al menos un segundo de canción.')
            try:
                if any(json.loads(path.read_text()).get('status') == 'rendering' for path in data().glob('*/video-*.json')):
                    raise HTTPException(409, 'Esperá a que termine la exportación de video.')
            except (OSError, ValueError):
                raise HTTPException(409, 'No se pudo comprobar el estado de los videos. Volvé a intentar.')
            if not slot.acquire(blocking=False):
                raise HTTPException(409, 'Ya se está animando un personaje. Esperá a que termine.')
            if not music_lock.acquire(blocking=False):
                slot.release()
                raise HTTPException(409, 'Esperá a que termine la creación de música o la copia actual.')
            job_id = uuid.uuid4().hex
            job = folder/'avatars'/job_id
            item = {**request.model_dump(), 'id': job_id, 'song_id': song_id, 'duration': duration,
                    'created': datetime.now(timezone.utc).isoformat(), 'status': 'rendering',
                    'percent': 0, 'message': 'Preparando el personaje…',
                    'url': f'/api/songs/{song_id}/avatars/{job_id}/video'}
            try:
                job.mkdir(parents=True)
                atomic_json(job/'job.json', item)
            except Exception:
                music_lock.release()
                slot.release()
                raise
            def progress(message, percent):
                item.update(message=message, percent=percent)
                atomic_json(job/'job.json', item)
            def worker():
                try:
                    run_avatar(job, source_path, folder/'audio.mp3', item, progress)
                    item.update(status='complete', percent=100, message='Personaje listo para ver y descargar')
                except Exception as error:
                    item.update(status='failed', message=str(error) if isinstance(error, RuntimeError) else 'No se pudo crear el personaje. Probá otra imagen o un fragmento más corto.')
                finally:
                    try:
                        atomic_json(job/'job.json', item)
                    finally:
                        music_lock.release()
                        slot.release()
            try:
                threading.Thread(target=worker, daemon=True).start()
            except Exception:
                item.update(status='failed', message='No se pudo iniciar la animación.')
                atomic_json(job/'job.json', item)
                music_lock.release()
                slot.release()
                raise
            return item

    @app.get('/api/songs/{song_id}/avatars/{job_id}/video')
    def video(song_id: str, job_id: str, download: bool = False):
        validate_id(job_id)
        path = song_folder(song_id)/'avatars'/job_id/'video.mp4'
        if not path.is_file():
            raise HTTPException(404, 'La animación todavía no está lista.')
        return FileResponse(path, media_type='video/mp4', filename='personaje-cantando.mp4' if download else None)
