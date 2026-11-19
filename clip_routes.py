"""Import short video scenes and expose the installed local Wan launcher."""
import json
import os
import platform
import subprocess
import uuid
from pathlib import Path

from fastapi import File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from library_routes import LIBRARY_LOCK, validate_id
from video_renderer import ffmpeg_binary


def register_clip_routes(app, data):
    """Store bounded scene clips inside each song; never accept arbitrary server paths."""
    def folder_for(song_id):
        validate_id(song_id)
        folder = data() / song_id
        if not (folder / "song.json").is_file():
            raise HTTPException(404, "Canción no encontrada")
        return folder

    @app.get("/api/wan/status")
    def wan_status():
        installed = platform.system() == "Darwin" and (Path.home() / "Applications/Wan Video.app").is_dir()
        return {"installed": installed}

    @app.post("/api/wan/open")
    def open_wan():
        if not wan_status()["installed"]:
            raise HTTPException(409, "Wan Video no está instalado en esta computadora.")
        result = subprocess.run(["/usr/bin/open", str(Path.home() / "Applications/Wan Video.app")], capture_output=True, timeout=10)
        if result.returncode:
            raise HTTPException(500, "Abrí Wan Video desde Aplicaciones.")
        return {"ok": True}

    @app.get("/api/songs/{song_id}/clips")
    def clips(song_id: str):
        with LIBRARY_LOCK:
            folder = folder_for(song_id) / "clips"
            return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]

    @app.post("/api/songs/{song_id}/clips", status_code=201)
    async def upload_clip(song_id: str, file: UploadFile = File(...)):
        with LIBRARY_LOCK:
            folder = folder_for(song_id) / "clips"
            folder.mkdir(exist_ok=True)
            if len(list(folder.glob("*.mp4"))) >= 20:
                raise HTTPException(409, "Esta canción ya tiene 20 escenas guardadas.")
        content = await file.read(100 * 1024 * 1024 + 1)
        name = Path(file.filename or "Escena IA").name[:100]
        await file.close()
        if len(content) > 100 * 1024 * 1024:
            raise HTTPException(413, "El clip puede pesar hasta 100 MB.")
        clip_id = uuid.uuid4().hex
        # Hold the folder steady while inspecting and normalizing a clip.
        with LIBRARY_LOCK:
            folder = folder_for(song_id) / "clips"
            folder.mkdir(exist_ok=True)
            source = folder / f"{clip_id}.upload"
            output = folder / f"{clip_id}.mp4"
            try:
                source.write_bytes(content)
                binary = ffmpeg_binary()
                probe = str(Path(binary).with_name("ffprobe"))
                info = subprocess.run([probe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)], capture_output=True, text=True, timeout=15, check=True)
                info = json.loads(info.stdout)
                stream = next(s for s in info["streams"] if s["codec_type"] == "video")
                duration = float(info["format"]["duration"])
                if not .2 <= duration <= 30 or max(stream["width"], stream["height"]) > 4096:
                    raise ValueError("El clip debe durar entre 0,2 y 30 segundos y medir como máximo 4096 píxeles.")
                result = subprocess.run([binary, "-loglevel", "error", "-y", "-i", str(source), "-map", "0:v:0", "-an", "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2,fps=24", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(output)], capture_output=True, timeout=120)
                if result.returncode:
                    raise ValueError("No se pudo convertir el clip.")
                item = {"id": clip_id, "name": name, "duration": duration, "url": f"/api/songs/{song_id}/clips/{clip_id}"}
                (folder / f"{clip_id}.json").write_text(json.dumps(item, ensure_ascii=False))
                return item
            except (ValueError, OSError, StopIteration, KeyError, subprocess.SubprocessError):
                output.unlink(missing_ok=True)
                raise HTTPException(422, "Subí un video válido de 0,2 a 30 segundos (MP4, MOV o WebM).")
            finally:
                source.unlink(missing_ok=True)

    @app.get("/api/songs/{song_id}/clips/{clip_id}")
    def clip_file(song_id: str, clip_id: str):
        validate_id(clip_id)
        path = folder_for(song_id) / "clips" / f"{clip_id}.mp4"
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="video/mp4")
