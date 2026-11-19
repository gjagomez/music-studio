"""Local video jobs, persisted independently from song generation metadata."""
import json
import threading
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from video_renderer import ffmpeg_binary, render_video, audio_duration
from library_routes import LIBRARY_LOCK
from lyrics_timing import parse_lrc


class VideoRequest(BaseModel):
    """The output format and appearance of a music video."""
    layout: Literal["horizontal", "vertical"] = "horizontal"
    palette: Literal["gold", "forest", "blue"] = "gold"
    channel: str = Field(default="", max_length=80)
    title: str = Field(default="", max_length=100)
    start: float = Field(default=0, ge=0, le=600)
    end: float | None = Field(default=None, gt=0, le=600)
    volume: float = Field(default=1, ge=0, le=2)
    lyrics_lrc: str = Field(default="", max_length=30000)
    screen_text: str = Field(default="", max_length=300)
    text_position: Literal["top", "center", "bottom"] = "bottom"
    text_size: Literal["small", "medium", "large"] = "medium"
    text_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    waveform: bool = True
    visualizer: Literal["lightning", "classic"] = "lightning"
    wave_color: str = Field(default="#705bff", pattern=r"^#[0-9a-fA-F]{6}$")
    images: list[str] = Field(default_factory=list, max_length=8)
    motion: bool = True
    overlay_text: bool = True
    clip: str = Field(default="", max_length=32)

    @model_validator(mode="after")
    def valid_trim(self):
        """Reject reversed or empty trim intervals."""
        cues = parse_lrc(self.lyrics_lrc)
        if self.end is not None and any(c["start"] >= 600 for c in cues):
            raise ValueError("Los tiempos deben estar dentro de la canción.")
        if self.end is not None and self.end <= self.start:
            raise ValueError("El final debe ser mayor que el inicio.")
        if any(len(i) != 32 or any(c not in "0123456789abcdef" for c in i) for i in self.images):
            raise ValueError("Las imágenes deben ser archivos subidos a esta canción.")
        if self.clip and (len(self.clip) != 32 or any(c not in "0123456789abcdef" for c in self.clip)):
            raise ValueError("La escena debe ser un video subido a esta canción.")
        return self


def register_video_routes(app, data):
    """Attach video endpoints using a dynamic library path for production and tests."""
    slot = threading.Lock()
    for path in data().glob("*/video-*.json"):
        try:
            state = json.loads(path.read_text())
            if state["status"] == "rendering":
                state.update(status="failed", message="El video se interrumpió. Volvé a crearlo.")
                path.write_text(json.dumps(state))
        except (OSError, ValueError, KeyError):
            continue

    def folder_for(song_id):
        if len(song_id) != 32 or any(c not in "0123456789abcdef" for c in song_id):
            raise HTTPException(404, "Canción no encontrada")
        folder = data() / song_id
        if not (folder / "audio.mp3").is_file() or not (folder / "song.json").is_file():
            raise HTTPException(404, "La canción todavía no tiene audio")
        return folder

    def save(folder, state):
        path = folder / f'video-{state["layout"]}.json'
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, ensure_ascii=False))
        temp.replace(path)

    def worker(folder, song, request):
        state = {**request.model_dump(), "status": "rendering", "message": "Creando el video…"}
        try:
            render_video(folder, song, request.layout, request.palette, request.channel, request.model_dump())
            state.update(status="complete", message="Video listo para descargar")
        except Exception as error:
            message = str(error) if isinstance(error, RuntimeError) else "No se pudo crear el video. Volvé a intentar."
            state.update(status="failed", message=message)
        finally:
            try:
                save(folder, state)
            finally:
                slot.release()

    @app.get("/api/videos/status")
    def statuses():
        items = []
        for path in data().glob("*/video-*.json"):
            try:
                state = json.loads(path.read_text())
                items.append({**state, "song_id": path.parent.name})
            except (OSError, ValueError):
                continue
        try:
            ffmpeg_binary()
            available = True
        except RuntimeError:
            available = False
        return {"items": items, "busy": slot.locked(), "available": available}

    @app.post("/api/songs/{song_id}/video", status_code=202)
    def create_video(song_id: str, request: VideoRequest):
        with LIBRARY_LOCK:
            folder = folder_for(song_id)
            cues = parse_lrc(request.lyrics_lrc)
            if cues and cues[-1]["start"] >= audio_duration(folder / "audio.mp3"):
                raise HTTPException(422, "Una frase empieza después del final de la canción.")
            if request.clip and not (folder / "clips" / f"{request.clip}.mp4").is_file():
                raise HTTPException(422, "La escena de video no está disponible.")
            if any(not (folder / "images" / f"{i}.jpg").is_file() for i in request.images):
                raise HTTPException(422, "Una de las imágenes ya no está disponible.")
            try:
                ffmpeg_binary()
                if request.start or request.end is not None:
                    duration = audio_duration(folder / "audio.mp3")
                    if request.start >= duration or (request.end is not None and request.end > duration + .1):
                        raise HTTPException(422, "El recorte debe quedar dentro de la duración de la canción.")
            except RuntimeError as error:
                raise HTTPException(503, str(error))
            if not slot.acquire(blocking=False):
                raise HTTPException(409, "Ya se está creando un video. Esperá a que termine.")
            state = {**request.model_dump(), "status": "rendering", "message": "Creando el video…"}
            try:
                song = json.loads((folder / "song.json").read_text())
                save(folder, state)
                threading.Thread(target=worker, args=(folder, song, request), daemon=True).start()
            except Exception:
                slot.release()
                raise
            return state

    @app.get("/api/songs/{song_id}/video/{layout}")
    def video_file(song_id: str, layout: Literal["horizontal", "vertical"], download: bool = False):
        folder = folder_for(song_id)
        path = folder / f"video-{layout}.mp4"
        if not path.is_file():
            raise HTTPException(404, "El video aún no está listo")
        return FileResponse(path, media_type="video/mp4", filename=f"cancion-{layout}.mp4" if download else None)
