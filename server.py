"""Local Spanish music studio with a persistent library and one generation at a time."""
import json
import os
import shutil
import secrets
import wave
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal

from avatar_routes import register_avatar_routes
from studio_features import register_studio_features
from library_routes import LIBRARY_LOCK, validate_id
from melody_routes import register_melody_routes
from engine import generate
from audio_export import download_mp3
from styles import PRESETS
from video_routes import register_video_routes
from library_routes import register_library_routes
from media_routes import register_media_routes
from clip_routes import register_clip_routes

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
lock = threading.Lock()
app = FastAPI(docs_url=None, redoc_url=None)


class Song(BaseModel):
    """The user-facing controls accepted for one generation."""
    title: str = Field(min_length=1, max_length=100)
    style: str = Field(min_length=1, max_length=40)
    instrumental: bool = False
    voice: Literal["male", "female"] = "male"
    language: Literal["es", "en"] = "es"
    mood: Literal["sincero", "alegre", "emotivo", "tranquilo", "energico"] = "sincero"
    description: str = Field(default="", max_length=2000)
    lyrics: str = Field(default="", max_length=5000)
    duration: Literal[15, 30, 60, 120, 180, 240, 300] = 30
    melody_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    melody_strength: float = Field(default=0.6, ge=0.1, le=1.0)
    quality: Literal["fast", "standard"] = "standard"
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)
    bpm: int = Field(default=90, ge=60, le=160)


def save(folder: Path, item: dict) -> None:
    """Atomically persist a song and its generation state."""
    temp = folder / "song.tmp"
    temp.write_text(json.dumps(item, ensure_ascii=False, indent=2))
    temp.replace(folder / "song.json")


def read_songs() -> list:
    """Return saved songs, newest first."""
    items = []
    for path in DATA.glob("*/song.json"):
        try:
            item = json.loads(path.read_text())
            item.setdefault("language", "es")
            organization = path.parent / "organization.json"
            if organization.is_file():
                item.update({k: v for k, v in json.loads(organization.read_text()).items() if k in {"favorite", "collection", "notes"}})
            items.append(item)
        except (OSError, ValueError):
            continue
    return sorted(items, key=lambda item: item["created"], reverse=True)


def worker(folder: Path, item: dict, release_slot: bool = True) -> None:
    """Run one generation, preserving both successful and failed library entries."""
    def progress(message):
        item.update(message=message)
        save(folder, item)
    try:
        generate(item, folder, progress)
        item.update(status="complete", message="Lista para escuchar")
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else "No se pudo conectar con ACE-Step. Abrí el motor y volvé a intentar."
        item.update(status="failed", message=message)
    finally:
        try:
            save(folder, item)
        finally:
            if release_slot:
                lock.release()


@app.middleware("http")
async def local_requests(request: Request, call_next):
    """Restrict mutations to this local interface."""
    if request.headers.get("host", "").split(":")[0] not in {"127.0.0.1", "localhost", "testserver"}:
        return JSONResponse({"detail": "Acceso local únicamente"}, status_code=403)
    if request.method in {"POST", "DELETE"} and request.headers.get("x-studio") != "ace-local":
        return JSONResponse({"detail": "Recargá la página"}, status_code=403)
    return await call_next(request)


@app.get("/api/health")
def health():
    """Check whether ACE-Step has finished starting."""
    try:
        ready = requests.get("http://127.0.0.1:7860/config", timeout=2).ok
    except requests.RequestException:
        ready = False
    return {"ready": ready, "busy": lock.locked(), "activity": "avatar" if getattr(app.state, "avatar_busy", lambda: False)() else "music" if lock.locked() else None}


@app.get("/api/songs")
def songs():
    """List library metadata without exposing server file paths."""
    return read_songs()


@app.post("/api/songs", status_code=202)
def create_song(song: Song):
    """Start one song after validating the request and claiming the generation slot."""
    if song.style not in PRESETS:
        raise HTTPException(422, "Elegí un estilo de la lista.")
    if not song.instrumental and song.style != "instrumental" and not song.lyrics.strip():
        raise HTTPException(422, "Escribí la letra para la canción con voz.")
    if song.melody_id and not (DATA / "_melodies" / (song.melody_id + ".wav")).is_file():
        raise HTTPException(422, "Volvé a subir o grabar tu melodía.")
    if not lock.acquire(blocking=False):
        raise HTTPException(409, "Ya hay una canción en proceso. Esperá a que termine.")
    item = {**song.model_dump(), "id": uuid.uuid4().hex, "created": datetime.now(timezone.utc).isoformat(),
            "status": "generating", "message": "Preparando la canción…"}
    if song.melody_id:
        with wave.open(str(DATA / "_melodies" / (song.melody_id + ".wav"))) as guide:
            item["melody_duration"] = round(guide.getnframes() / guide.getframerate(), 2)
    if item["seed"] is None:
        item["seed"] = secrets.randbelow(2**31)
    folder = DATA / item["id"]
    try:
        folder.mkdir()
        save(folder, item)
        threading.Thread(target=worker, args=(folder, item), daemon=True).start()
    except Exception:
        lock.release()
        raise
    return item


class VariantsRequest(BaseModel):
    count: Literal[2, 3] = 2


@app.post('/api/songs/{song_id}/variants', status_code=202)
def create_variants(song_id: str, request: VariantsRequest):
    validate_id(song_id)
    with LIBRARY_LOCK:
        source = DATA / song_id / 'song.json'
        if not source.is_file():
            raise HTTPException(404, 'Canción no encontrada.')
        original = json.loads(source.read_text())
        if original.get('audio_edit'):
            raise HTTPException(422, 'Creá las versiones desde la canción original, antes del recorte.')
        if original.get('status') not in {'complete', 'failed'}:
            raise HTTPException(409, 'Esperá a que termine esta canción.')
        try:
            settings = Song.model_validate(original).model_dump()
        except ValueError:
            raise HTTPException(422, 'Reutilizá y guardá los ajustes de esta canción antes de crear versiones.')
        if settings['style'] not in PRESETS or (not settings['instrumental'] and settings['style'] != 'instrumental' and not settings['lyrics'].strip()):
            raise HTTPException(422, 'Completá el estilo y la letra antes de crear versiones.')
        if settings.get('melody_id') and not (DATA/'_melodies'/(settings['melody_id']+'.wav')).is_file():
            raise HTTPException(422, 'Volvé a seleccionar la melodía guía.')
        if not lock.acquire(blocking=False):
            raise HTTPException(409, 'Ya hay canciones en proceso. Esperá a que terminen.')
        group = uuid.uuid4().hex
        items = []
        try:
            for i in range(request.count):
                item = {**settings, 'id': uuid.uuid4().hex, 'parent_id': song_id, 'variation_group': group,
                        'title': (settings['title'][:88] + f' · Versión {i+1}'), 'seed': secrets.randbelow(2**31),
                        'created': datetime.now(timezone.utc).isoformat(), 'status': 'queued',
                        'message': f'En cola · versión {i+1} de {request.count}'}
                if original.get('melody_duration'):
                    item['melody_duration'] = original['melody_duration']
                folder = DATA/item['id']
                folder.mkdir()
                save(folder, item)
                items.append(item)
                organization_path = source.parent/'organization.json'
                if organization_path.is_file():
                    organization = json.loads(organization_path.read_text())
                    (folder/'organization.json').write_text(json.dumps({'favorite': False, 'collection': organization.get('collection', ''), 'notes': ''}))
            def batch_worker():
                try:
                    for item in items:
                        item.update(status='generating', message='Preparando esta versión…')
                        save(DATA/item['id'], item)
                        worker(DATA/item['id'], item, release_slot=False)
                finally:
                    try:
                        for item in items:
                            if item['status'] in {'queued', 'generating'}:
                                item.update(status='failed', message='Se interrumpió la cola. Podés volver a intentarlo.')
                                save(DATA/item['id'], item)
                    finally:
                        lock.release()
            threading.Thread(target=batch_worker, daemon=True).start()
        except Exception:
            for item in items:
                item.update(status='failed', message='No se pudo iniciar la cola.')
                save(DATA/item['id'], item)
            lock.release()
            raise
        return {'items': items, 'group': group}


@app.get("/api/songs/{song_id}/audio")
def audio(song_id: str, download: bool = False):
    """Serve only an MP3 inside an identified library entry."""
    if len(song_id) != 32 or any(c not in "0123456789abcdef" for c in song_id):
        raise HTTPException(404)
    path = DATA / song_id / "audio.mp3"
    if not path.is_file():
        raise HTTPException(404)
    if download:
        path = download_mp3(path)
    return FileResponse(path, media_type="audio/mpeg", filename="cancion.mp3" if download else None)


def initialize_library():
    """Recover interrupted jobs and import prior demos once, leaving originals intact."""
    for item in read_songs():
        if item["status"] in {"generating", "queued"}:
            item.update(status="failed", message="Se interrumpió al cerrar el estudio. Podés crear otra versión.")
            save(DATA / item["id"], item)
    if os.environ.get("ALMA_IMPORT_DEMOS") != "1":
        return
    for path in (Path.home() / "Music/ACE-Step").glob("*/*.mp3"):
        song_id = uuid.uuid5(uuid.NAMESPACE_URL, str(path)).hex
        folder = DATA / song_id
        if folder.exists():
            continue
        folder.mkdir()
        shutil.copy2(path, folder / "audio.mp3")
        lyrics = path.parent / "letra.txt"
        save(folder, {"id": song_id, "title": path.stem.replace("caminare", "caminaré"),
             "style": "banda-cristiana", "voice": "male", "description": "Prueba creada en ACE-Step",
             "lyrics": lyrics.read_text() if lyrics.exists() else "", "duration": 30, "bpm": 90,
             "created": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
             "status": "complete", "message": "Lista para escuchar"})


initialize_library()
register_avatar_routes(app, lambda: DATA, lock)
register_studio_features(app, lambda: DATA, lock)
register_melody_routes(app, lambda: DATA)
register_video_routes(app, lambda: DATA)
register_library_routes(app, lambda: DATA)
register_media_routes(app, lambda: DATA)
register_clip_routes(app, lambda: DATA)
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
