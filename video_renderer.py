"""Render a local music visualizer with an original typographic cover and FFmpeg."""
import shutil
import subprocess
import textwrap
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

SIZES = {"horizontal": (1280, 720), "vertical": (720, 1280)}
PALETTES = {
    "gold": ((21, 23, 19), (84, 63, 37), "#e9ad68"),
    "forest": ((12, 28, 25), (34, 80, 62), "#a5d1ac"),
    "blue": ((16, 22, 44), (44, 61, 101), "#9dbded"),
}


def ffmpeg_binary():
    """Find FFmpeg without assuming a particular operating system or shell PATH."""
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    for path in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if Path(path).is_file():
            return path
    raise RuntimeError("Falta FFmpeg. Instalalo y volvé a abrir Alma.")


def font(size):
    """Load an installed font with Spanish accents, falling back to Pillow's default."""
    for name in ("/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def draw_screen_text(image, layout, options):
    """Render an independent caption without interpolating user text into FFmpeg."""
    text = options.get("screen_text", "").strip()
    if not text:
        return
    width, height = SIZES[layout]
    draw = ImageDraw.Draw(image)
    size = {"small": 24, "medium": 32, "large": 44}.get(options.get("text_size"), 32)
    while True:
        caption_font = font(size)
        lines = []
        for paragraph in text.splitlines():
            lines.extend(textwrap.wrap(paragraph, max(10, int(width*.84/(size*.58)))) or [""])
        content = "\n".join(lines)
        box = draw.multiline_textbbox((0, 0), content, font=caption_font, spacing=6)
        if size <= 12 or (box[2]-box[0] <= width*.88 and box[3]-box[1] <= height*.22):
            break
        size -= 1
    bottom = .63 if options.get('lyrics_lrc', '').strip() else .91
    y = {"top": .15, "center": .58, "bottom": bottom}.get(options.get("text_position"), bottom)
    # Keep long captions inside the picture, including the lower safe area.
    half = (box[3]-box[1])/2
    center_y = min(height-half-height*.04, max(half+height*.04, height*y))
    draw.multiline_text((width/2, center_y), content, font=caption_font,
                        fill=options.get("text_color", "#ffffff"), stroke_width=1,
                        stroke_fill="#000000", anchor="mm", align="center", spacing=6)


def cover(title, channel, layout, palette, target, background=None, show_text=True, visualizer="classic", accent_override=None, text_options=None):
    """Draw a fitted title and abstract album artwork without external assets."""
    width, height = SIZES[layout]
    dark, glow, accent = PALETTES[palette]
    accent = accent_override or accent
    if visualizer == "lightning":
        dark = (9, 10, 17)
        glow = tuple(int(accent[i:i+2], 16)//3 for i in (1, 3, 5))
    if background:
        with Image.open(background) as original:
            image = ImageOps.fit(original.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
        if show_text:
            image = Image.blend(image, Image.new("RGB", image.size, "black"), .32)
        draw = ImageDraw.Draw(image)
    else:
        image = Image.new("RGB", (width, height), dark)
        draw = ImageDraw.Draw(image)
        for y in range(height):
            mix = y / height * 0.6
            color = tuple(int(a + (b - a) * mix) for a, b in zip(dark, glow))
            draw.line((0, y, width, y), fill=color)
        haze = Image.new("RGBA", image.size)
        hd = ImageDraw.Draw(haze)
        hd.ellipse((width * .25, -height * .25, width * 1.3, height * .7), fill=(*glow, 140))
        image = Image.alpha_composite(image.convert("RGBA"), haze.filter(ImageFilter.GaussianBlur(90)))
        draw = ImageDraw.Draw(image)
        center = (width // 2, int(height * .29))
        radius = int(min(width, height) * .13)
        for factor in (() if visualizer == "lightning" else (1, .84, .68)):
            r = radius * factor
            draw.ellipse((center[0]-r, center[1]-r, center[0]+r, center[1]+r), outline=(*glow, 230), width=2)
        r = int(radius * .35)
        if visualizer != "lightning":
            draw.ellipse((center[0]-r, center[1]-r, center[0]+r, center[1]+r), fill=accent)
            draw.ellipse((center[0]-5, center[1]-5, center[0]+5, center[1]+5), fill=dark)
    if not show_text:
        draw_screen_text(image, layout, text_options or {})
        image.convert("RGB").save(target)
        return
    title_size = 53 if layout == "horizontal" else 48
    max_width = width * .83
    while True:
        title_font = font(title_size)
        lines = textwrap.wrap(title, width=max(12, int(max_width / (title_size * .55))))
        if len(lines) <= 3 and all(draw.textlength(line, font=title_font) <= max_width for line in lines):
            break
        title_size -= 2
        if title_size < 20:
            break
    draw.multiline_text((width/2, height*(.40 if visualizer == "lightning" else .49)), "\n".join(lines), font=title_font, fill="#f6f0e3", anchor="mm", align="center", spacing=12)
    if channel:
        channel_font = font(22)
        while draw.textlength(channel, font=channel_font) > max_width:
            channel = channel[:-2] + "…"
        draw.text((width/2, height*(.53 if visualizer == "lightning" else .625)), channel, font=channel_font, fill=accent, anchor="mm")
    draw_screen_text(image, layout, text_options or {})
    image.convert("RGB").save(target)


def audio_duration(path):
    """Read the actual audio duration to validate editor trim boundaries."""
    binary = ffmpeg_binary()
    probe = shutil.which("ffprobe") or str(Path(binary).with_name("ffprobe"))
    result = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=30)
    return float(json.loads(result.stdout)["format"]["duration"])


def render_video(folder, song, layout, palette, channel, options=None):
    """Create a browser-compatible MP4 atomically; keep existing exports on failure."""
    folder = Path(folder).resolve()
    binary = ffmpeg_binary()
    width, height = SIZES[layout]
    options = options or {}
    from scene_renderer import scene_inputs
    duration = (options.get("end") if options.get("end") is not None else audio_duration(folder / "audio.mp3")) - options.get("start", 0)
    image_inputs, background_filters, audio_index = scene_inputs(folder, song, layout, palette, channel, options, duration)
    if options.get("lyrics_lrc", "").strip():
        from lyrics_timing import timed_overlay_input
        image_inputs += timed_overlay_input(folder, options["lyrics_lrc"], options.get("start", 0),
                                            options.get("start", 0)+duration, layout, options.get("text_color", "#ffffff"))
        background_filters = background_filters.replace("[background]", "[beforelyrics]")
        background_filters += f";[{audio_index}:v]fps=24,setpts=PTS-STARTPTS,format=rgba[lyric];[beforelyrics][lyric]overlay=0:0:shortest=1[background]"
        audio_index += 1
    lightning = options.get("visualizer", "lightning") == "lightning"
    wave_width = width if lightning else int(width * .82) // 2 * 2
    wave_height = (170 if layout == "horizontal" else 230) if lightning else (105 if layout == "horizontal" else 150)
    color = options.get("wave_color", "#705bff") if lightning else PALETTES[palette][2]
    # Defend direct Python callers as well as the validated HTTP endpoint.
    import re
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("El color debe tener formato hexadecimal #RRGGBB.")
    accent = color.replace("#", "0x")
    wave = (f"[{audio_index}:a]aformat=channel_layouts=mono,showwaves=s={wave_width}x{wave_height}:"
            f"mode={'p2p' if lightning else 'cline'}:rate=24:colors={accent}:scale=sqrt:draw=full,format=rgba")
    if lightning:
        filters = (wave + ",split=2[core][halo];[halo]dilation,dilation,dilation,gblur=sigma=7:steps=2[glow];"
                   "[background][glow]overlay=0:H*0.68:shortest=1[lit];"
                   "[lit][core]overlay=0:H*0.68:shortest=1,format=yuv420p[v]")
    else:
        filters = wave + "[wave];[background][wave]overlay=(W-w)/2:H*0.73:shortest=1,format=yuv420p[v]"
    if not options.get("waveform", True):
        filters = "[background]format=yuv420p[v]"
    volume = float(options.get("volume", 1))
    filters = background_filters + ";" + filters + f";[{audio_index}:a]volume={volume}[a]"
    output = folder / f"video-{layout}.mp4"
    temp = folder / f"video-{layout}.partial.mp4"
    command = [binary, "-hide_banner", "-loglevel", "error", "-y", "-filter_complex_threads", "2"] + image_inputs
    command += ["-ss", str(options.get("start", 0))]
    if options.get("end") is not None:
        command += ["-t", str(options["end"] - options.get("start", 0))]
    command += ["-i", str(folder / "audio.mp3"), "-filter_complex", filters,
               "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "22", "-threads", "2", "-c:a", "aac", "-b:a", "192k",
               "-t", str(duration), "-shortest", "-movflags", "+faststart", str(temp)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600, cwd=folder)
        if result.returncode:
            (folder / "video-error.log").write_text(result.stderr)
            raise RuntimeError("No se pudo exportar el video. Revisá que FFmpeg incluya libx264 y showwaves.")
        temp.replace(output)
        return output
    finally:
        temp.unlink(missing_ok=True)
