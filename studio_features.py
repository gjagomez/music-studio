"""Library organization, reversible audio edits and portable local backups."""
import json
import math
import zlib
import shutil
import stat
import subprocess
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from fastapi import HTTPException, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from library_routes import LIBRARY_LOCK, validate_id
from video_renderer import ffmpeg_binary, audio_duration


class Organization(BaseModel):
    favorite: bool = False
    collection: str = Field(default='', max_length=60)
    notes: str = Field(default='', max_length=2000)


class AudioEdit(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    start: float = Field(default=0, ge=0, le=600)
    end: float = Field(gt=0, le=600)
    volume: float = Field(default=1, ge=0, le=2)
    fade_in: float = Field(default=.15, ge=0, le=10)
    fade_out: float = Field(default=1, ge=0, le=10)

    @model_validator(mode='after')
    def valid_range(self):
        duration = self.end-self.start
        if duration < .25:
            raise ValueError('El recorte debe durar al menos 0,25 segundos.')
        if self.fade_in+self.fade_out > duration:
            raise ValueError('La entrada y la salida suaves deben caber dentro del recorte.')
        return self


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def register_studio_features(app, data, music_lock):
    slot = threading.Lock()
    for path in data().glob('*/edit-job.json'):
        try:
            state = json.loads(path.read_text())
            if state.get('status') == 'rendering':
                atomic_json(path, {**state, 'status': 'failed', 'message': 'La edición se interrumpió. Podés volver a crearla.'})
        except (OSError, ValueError):
            pass

    def folder_for(song_id):
        validate_id(song_id)
        folder = data()/song_id
        if not (folder/'song.json').is_file():
            raise HTTPException(404, 'Canción no encontrada.')
        return folder

    def library_idle():
        if slot.locked():
            raise HTTPException(409, 'Esperá a que termine la edición o copia actual.')
        for path in data().glob('*/video-*.json'):
            if json.loads(path.read_text()).get('status') == 'rendering':
                raise HTTPException(409, 'Esperá a que termine la exportación del video.')

    @app.post('/api/songs/{song_id}/organization')
    def organize(song_id: str, request: Organization):
        with LIBRARY_LOCK:
            folder = folder_for(song_id)
            value = request.model_dump()
            value['collection'] = value['collection'].strip()
            atomic_json(folder/'organization.json', value)
            return value

    @app.get('/api/songs/{song_id}/edit-info')
    def edit_info(song_id: str):
        with LIBRARY_LOCK:
            folder = folder_for(song_id)
            if not (folder/'audio.mp3').is_file():
                raise HTTPException(409, 'La canción todavía no tiene audio.')
            return {'duration': audio_duration(folder/'audio.mp3')}

    @app.post('/api/songs/{song_id}/audio-edits', status_code=202)
    def edit_audio(song_id: str, request: AudioEdit):
        with LIBRARY_LOCK:
            source = folder_for(song_id)
            song = json.loads((source/'song.json').read_text())
            if song.get('status') != 'complete':
                raise HTTPException(409, 'Elegí una canción terminada.')
            actual = audio_duration(source/'audio.mp3')
            if request.end > actual+.05 or request.start >= actual:
                raise HTTPException(422, 'El recorte debe quedar dentro de la canción.')
            if not slot.acquire(blocking=False):
                raise HTTPException(409, 'Ya hay una edición o copia en proceso.')
            target = data()/uuid.uuid4().hex
            item = {**song, 'id': target.name, 'title': request.title.strip(), 'duration': round(request.end-request.start, 3),
                    'created': datetime.now(timezone.utc).isoformat(), 'status': 'generating',
                    'message': 'Editando el audio…', 'parent_id': song_id, 'audio_edit': request.model_dump(),
                    'melody_id': None, 'melody_duration': None}
            try:
                target.mkdir()
                atomic_json(target/'song.json', item)
                if (source/'organization.json').is_file():
                    organization = Organization.model_validate_json((source/'organization.json').read_text())
                    atomic_json(target/'organization.json', {'favorite': False, 'collection': organization.collection, 'notes': ''})
                atomic_json(source/'edit-job.json', {'status': 'rendering', 'target_id': target.name})
            except Exception:
                slot.release()
                raise
            def worker():
                try:
                    duration = request.end-request.start
                    filters = [f'volume={request.volume}']
                    if request.fade_in:
                        filters.append(f'afade=t=in:st=0:d={request.fade_in}')
                    if request.fade_out:
                        filters.append(f'afade=t=out:st={duration-request.fade_out}:d={request.fade_out}')
                    temporary = target/'edited.partial.mp3'
                    subprocess.run([ffmpeg_binary(), '-nostdin', '-v', 'error', '-y', '-ss', str(request.start),
                                    '-i', str(source/'audio.mp3'), '-t', str(duration), '-map', '0:a:0', '-af', ','.join(filters),
                                    '-c:a', 'libmp3lame', '-b:a', '320k', '-ar', '44100', str(temporary)],
                                   capture_output=True, check=True, timeout=180)
                    temporary.replace(target/'audio.mp3')
                    item.update(status='complete', message='Edición lista; el original está conservado')
                except Exception:
                    item.update(status='failed', message='No se pudo editar el audio. El original está conservado.')
                finally:
                    try:
                        with LIBRARY_LOCK:
                            atomic_json(target/'song.json', item)
                            atomic_json(source/'edit-job.json', {'status': item['status'], 'target_id': target.name})
                    finally:
                        slot.release()
            try:
                threading.Thread(target=worker, daemon=True).start()
            except Exception:
                item.update(status='failed', message='No se pudo iniciar la edición.')
                atomic_json(target/'song.json', item)
                atomic_json(source/'edit-job.json', {'status': 'failed'})
                slot.release()
                raise
            return item

    def backup_paths():
        for folder in data().iterdir():
            if folder.is_symlink() or not folder.is_dir():
                continue
            if (len(folder.name) == 32 and all(c in '0123456789abcdef' for c in folder.name)) or folder.name in {'.trash', '_melodies'}:
                for path in folder.rglob('*'):
                    if path.is_file() and not path.is_symlink() and not any(p.is_symlink() for p in path.parents if p != data() and p.is_relative_to(data())):
                        if path.suffix not in {'.tmp', '.log'} and '.partial.' not in path.name:
                            yield path

    @app.get('/api/backup')
    def backup():
        with LIBRARY_LOCK:
            library_idle()
            if not music_lock.acquire(blocking=False):
                raise HTTPException(409, 'Esperá a que terminen las canciones antes de copiar la biblioteca.')
            try:
                root = data()/'_backups'
                root.mkdir(exist_ok=True)
                stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                target = root/f'alma-{stamp}-{uuid.uuid4().hex[:6]}.zip'
                temporary = target.with_suffix('.partial')
                try:
                    with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
                        archive.writestr('manifest.json', json.dumps({'format': 'alma-backup-v1', 'created': stamp}))
                        for path in backup_paths():
                            archive.write(path, path.relative_to(data()).as_posix())
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
            finally:
                music_lock.release()
        return FileResponse(target, media_type='application/zip', filename=target.name)

    @app.post('/api/backup/create')
    def create_backup():
        response = backup()
        return {'name': response.filename, 'url': '/api/backups/'+response.filename}

    @app.get('/api/backups/{name}')
    def backup_file(name: str):
        import re
        if not re.fullmatch(r'alma-\d{8}-\d{6}-[0-9a-f]{6}\.zip', name):
            raise HTTPException(404)
        path = data()/'_backups'/name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type='application/zip', filename=name)

    @app.post('/api/backup/restore')
    def restore_backup(file: UploadFile = File(...)):
        with LIBRARY_LOCK:
            library_idle()
            if not music_lock.acquire(blocking=False):
                raise HTTPException(409, 'Esperá a que terminen las canciones antes de restaurar.')
            try:
                with tempfile.TemporaryDirectory(dir=data(), prefix='.restore-') as temporary:
                    root = Path(temporary)
                    uploaded = root/'backup.zip'
                    size = 0
                    with uploaded.open('wb') as output:
                        while chunk := file.file.read(1024*1024):
                            size += len(chunk)
                            if size > 2*1024**3:
                                raise HTTPException(413, 'El ZIP puede pesar hasta 2 GB.')
                            output.write(chunk)
                    with zipfile.ZipFile(uploaded) as archive:
                        entries = archive.infolist()
                        if len(entries) > 20000 or sum(e.file_size for e in entries) > 4*1024**3:
                            raise ValueError('Copia demasiado grande.')
                        if archive.getinfo('manifest.json').file_size > 4096:
                            raise ValueError('Manifiesto demasiado grande.')
                        if json.loads(archive.read('manifest.json')).get('format') != 'alma-backup-v1':
                            raise ValueError('No es una copia de Alma.')
                        unpacked = root/'library'
                        unpacked.mkdir()
                        for entry in entries:
                            if entry.filename == 'manifest.json' or entry.is_dir():
                                continue
                            relative = PurePosixPath(entry.filename)
                            parts = relative.parts
                            if (relative.is_absolute() or '..' in parts or '\\' in entry.filename
                                    or stat.S_ISLNK(entry.external_attr >> 16) or len(parts) < 2):
                                raise ValueError('Ruta no válida en la copia.')
                            first = parts[0]
                            if first not in {'.trash', '_melodies'}:
                                if len(first) != 32 or any(c not in '0123456789abcdef' for c in first):
                                    raise ValueError('Identificador no válido.')
                            elif first == '.trash':
                                if len(parts) < 3:
                                    raise ValueError('Papelera no válida.')
                                if len(parts[1]) != 32 or any(c not in '0123456789abcdef' for c in parts[1]):
                                    raise ValueError('Identificador no válido.')
                            else:
                                if len(parts) != 2 or not parts[1].endswith('.wav'):
                                    raise ValueError('Guía no válida.')
                                if len(parts[1][:-4]) != 32 or any(c not in '0123456789abcdef' for c in parts[1][:-4]):
                                    raise ValueError('Identificador de guía no válido.')
                            if parts[-1] in {'song.json', 'organization.json'} and entry.file_size > 1024*1024:
                                raise ValueError('Metadatos demasiado grandes.')
                            destination = unpacked.joinpath(*parts)
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            with archive.open(entry) as source, destination.open('wb') as output:
                                shutil.copyfileobj(source, output)
                    # Validate all song folders before moving any of them into the library.
                    song_folders = [p for p in unpacked.iterdir() if len(p.name) == 32]
                    trash_root = unpacked/'.trash'
                    if trash_root.exists():
                        song_folders += list(trash_root.iterdir())
                    for folder in song_folders:
                        item = json.loads((folder/'song.json').read_text())
                        if item.get('id') != folder.name or not isinstance(item.get('title'), str) or 'created' not in item:
                            raise ValueError('Metadatos de canción no válidos.')
                        if (not isinstance(item.get('style'), str) or not isinstance(item.get('created'), str)
                                or any(not isinstance(item.get(key, ''), str) for key in ['lyrics', 'description'])
                                or item.get('status') not in {'complete', 'failed', 'generating', 'queued'}):
                            raise ValueError('Contenido de canción no válido.')
                        duration = float(item.get('duration', 0))
                        if not math.isfinite(duration) or not 0 < duration <= 600:
                            raise ValueError('Duración no válida.')
                        organization = folder/'organization.json'
                        if organization.exists():
                            atomic_json(organization, Organization.model_validate_json(organization.read_text()).model_dump())
                        if item.get('status') in {'generating', 'queued'}:
                            item.update(status='failed', message='Trabajo interrumpido en la copia; podés crear otra versión.')
                            atomic_json(folder/'song.json', item)
                        for job in list(folder.glob('video-*.json'))+list(folder.glob('edit-job.json'))+list((folder/'avatars').glob('*/job.json')):
                            value = json.loads(job.read_text())
                            if value.get('status') == 'rendering':
                                atomic_json(job, {**value, 'status': 'failed', 'message': 'Exportación interrumpida en la copia.'})
                    restored, skipped = 0, 0
                    for folder in song_folders:
                        relative = folder.relative_to(unpacked)
                        destination = data()/relative
                        # An ID already present anywhere is kept exactly as it is.
                        if (data()/folder.name).exists() or (data()/'.trash'/folder.name).exists():
                            skipped += 1
                            continue
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        folder.rename(destination)
                        restored += 1
                    for guide in (unpacked/'_melodies').glob('*.wav'):
                        destination = data()/'_melodies'/guide.name
                        destination.parent.mkdir(exist_ok=True)
                        if not destination.exists():
                            guide.rename(destination)
                    return {'restored': restored, 'skipped': skipped}
            except (ValueError, TypeError, KeyError, OSError, EOFError, zlib.error, zipfile.BadZipFile, RuntimeError):
                raise HTTPException(422, 'La copia no es válida. Usá un ZIP exportado desde Alma.')
            finally:
                file.file.close()
                music_lock.release()
