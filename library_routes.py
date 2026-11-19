"""Reversible song removal coordinated with active video exports."""
import json
import threading
from datetime import datetime, timezone

from fastapi import HTTPException

LIBRARY_LOCK = threading.RLock()


def validate_id(song_id):
    """Reject values that cannot name a library folder."""
    if len(song_id) != 32 or any(c not in "0123456789abcdef" for c in song_id):
        raise HTTPException(404, "Canción no encontrada")


def register_library_routes(app, data):
    """Expose trash and restore operations, preserving external original audio."""
    @app.get("/api/trash")
    def trash():
        items = []
        for path in (data() / ".trash").glob("*/song.json"):
            try:
                item = json.loads(path.read_text())
                item["deleted_at"] = json.loads((path.parent / "trash.json").read_text())["deleted_at"]
                items.append(item)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(items, key=lambda item: item["deleted_at"], reverse=True)

    @app.delete("/api/songs/{song_id}")
    def delete_song(song_id: str):
        validate_id(song_id)
        with LIBRARY_LOCK:
            folder = data() / song_id
            if not (folder / "song.json").is_file():
                raise HTTPException(404, "Canción no encontrada")
            item = json.loads((folder / "song.json").read_text())
            if item["status"] in {"generating", "queued"}:
                raise HTTPException(409, "Esperá a que termine la canción antes de eliminarla.")
            for path in list(folder.glob("video-*.json")) + list(folder.glob("edit-job.json")) + list((folder/"avatars").glob("*/job.json")):
                if json.loads(path.read_text()).get("status") == "rendering":
                    raise HTTPException(409, "Esperá a que termine el video antes de eliminar la canción.")
            target = data() / ".trash" / song_id
            target.parent.mkdir(exist_ok=True)
            if target.exists():
                raise HTTPException(409, "La canción ya está en la papelera.")
            (folder / "trash.json").write_text(json.dumps({"deleted_at": datetime.now(timezone.utc).isoformat()}))
            folder.rename(target)
        return {"ok": True, "message": "Canción y videos movidos a la papelera"}

    @app.post("/api/trash/{song_id}/restore")
    def restore(song_id: str):
        validate_id(song_id)
        with LIBRARY_LOCK:
            source = data() / ".trash" / song_id
            target = data() / song_id
            if not (source / "song.json").is_file():
                raise HTTPException(404, "Canción no encontrada en la papelera")
            if target.exists():
                raise HTTPException(409, "La canción ya existe en tu biblioteca")
            source.rename(target)
            (target / "trash.json").unlink(missing_ok=True)
        return {"ok": True}
