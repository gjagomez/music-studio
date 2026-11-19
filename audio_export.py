"""Prepare MP3 downloads at 320 kbps and exactly 44,100 Hz."""
import json
import subprocess
import tempfile
from pathlib import Path


def download_mp3(source: Path) -> Path:
    """Reuse a matching source or cache a converted download beside it."""
    probe = subprocess.run(
        ["/opt/homebrew/bin/ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,bit_rate,codec_name", "-of", "json", str(source)],
        capture_output=True, text=True, check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    if (stream.get("codec_name") == "mp3" and stream.get("sample_rate") == "44100"
            and stream.get("bit_rate") == "320000"):
        return source
    target = source.with_name("download-320k-44100.mp3")
    if target.is_file() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return target
    with tempfile.NamedTemporaryFile(dir=source.parent, suffix=".mp3", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        subprocess.run(
            ["/opt/homebrew/bin/ffmpeg", "-v", "error", "-y", "-i", str(source),
             "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100",
             str(temporary)],
            capture_output=True, check=True,
        )
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
