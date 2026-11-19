"""Upload and serve normalized images for local music video scenes."""
import io
import uuid
import warnings

from fastapi import HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from library_routes import LIBRARY_LOCK, validate_id


def register_media_routes(app, data):
    """Keep uploaded images inside their song and validate pixels before decoding."""
    def folder_for(song_id):
        validate_id(song_id)
        folder = data() / song_id
        if not (folder / "song.json").is_file():
            raise HTTPException(404, "Canción no encontrada")
        return folder

    @app.get("/api/songs/{song_id}/images")
    def images(song_id: str):
        with LIBRARY_LOCK:
            folder = folder_for(song_id) / "images"
            return [{"id": p.stem, "url": f"/api/songs/{song_id}/images/{p.stem}"} for p in sorted(folder.glob("*.jpg"), key=lambda p: p.stat().st_mtime)]

    @app.post("/api/songs/{song_id}/images", status_code=201)
    async def upload(song_id: str, file: UploadFile = File(...)):
        content = await file.read(12 * 1024 * 1024 + 1)
        await file.close()
        if len(content) > 12 * 1024 * 1024:
            raise HTTPException(413, "Cada imagen puede pesar hasta 12 MB.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                image = Image.open(io.BytesIO(content))
                if image.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("Formato no admitido")
                if image.width * image.height > 24_000_000:
                    raise ValueError("Imagen demasiado grande")
                image = ImageOps.exif_transpose(image)
                image.thumbnail((1920, 1920))
                if image.mode in {"RGBA", "LA"}:
                    base = Image.new("RGB", image.size, "#151713")
                    base.paste(image, mask=image.getchannel("A"))
                    image = base
                else:
                    image = image.convert("RGB")
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise HTTPException(422, "Usá una imagen JPG, PNG o WebP válida, de hasta 24 megapíxeles.")
        with LIBRARY_LOCK:
            folder = folder_for(song_id) / "images"
            folder.mkdir(exist_ok=True)
            if len(list(folder.glob("*.jpg"))) >= 40:
                raise HTTPException(409, "Esta canción ya tiene 40 imágenes guardadas.")
            image_id = uuid.uuid4().hex
            image.save(folder / f"{image_id}.jpg", quality=92)
        return {"id": image_id, "url": f"/api/songs/{song_id}/images/{image_id}"}

    @app.get("/api/songs/{song_id}/images/{image_id}")
    def image_file(song_id: str, image_id: str):
        validate_id(image_id)
        path = folder_for(song_id) / "images" / f"{image_id}.jpg"
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")
