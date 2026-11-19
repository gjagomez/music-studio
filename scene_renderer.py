"""Build bounded FFmpeg image sequences with optional slow camera movement."""


def scene_inputs(folder, song, layout, palette, channel, options, duration):
    """Return image input arguments, background filters, and the audio input index."""
    from video_renderer import cover, SIZES

    if options.get("clip"):
        return clip_input(folder, song, layout, channel, options, duration)

    ids = options.get("images", [])
    width, height = SIZES[layout]
    title = options.get("title") or song["title"]
    selected = ids or [None]
    segment = duration / len(selected)
    inputs, filters, labels = [], [], []
    for index, image_id in enumerate(selected):
        poster = folder / f"scene-{layout}-{index}.png"
        source = folder / "images" / f"{image_id}.jpg" if image_id else None
        cover(title, channel, layout, palette, poster, source, options.get("overlay_text", True), visualizer=options.get("visualizer", "lightning"), accent_override=options.get("wave_color") if options.get("visualizer", "lightning") == "lightning" else None, text_options=options)
        inputs += ["-loop", "1", "-framerate", "24", "-i", str(poster)]
        motion = ""
        if options.get("motion", True) and image_id:
            motion = f"zoompan=z='min(max(zoom,pzoom)+0.00012,1.07)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps=24,"
        label = f"scene{index}"
        filters.append(f"[{index}:v]{motion}trim=duration={segment},setpts=PTS-STARTPTS,setsar=1[{label}]")
        labels.append(f"[{label}]")
    if len(labels) == 1:
        filters.append(f"{labels[0]}null[background]")
    else:
        filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[background]")
    return inputs, ";".join(filters), len(selected)


def clip_input(folder, song, layout, channel, options, duration):
    """Loop a generated scene while keeping the song's audio and optional title overlay."""
    from PIL import Image, ImageDraw
    from video_renderer import SIZES, font, draw_screen_text
    import textwrap

    width, height = SIZES[layout]
    clip = folder / "clips" / f'{options["clip"]}.mp4'
    inputs = ["-stream_loop", "-1", "-i", str(clip)]
    filters = f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=24,trim=duration={duration},setpts=PTS-STARTPTS,setsar=1[clipbg]"
    if options.get("overlay_text", True) or options.get("screen_text", "").strip():
        overlay = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(overlay)
        title = "\n".join(textwrap.wrap(options.get("title") or song["title"], 28 if layout == "vertical" else 44))
        if options.get("overlay_text", True):
            draw.multiline_text((width/2, height*.49), title, font=font(40), fill="white", stroke_width=2, stroke_fill="#000a", anchor="mm", align="center")
            draw.text((width/2, height*.625), channel, font=font(22), fill=options.get("wave_color", "#e9ad68"), stroke_width=1, stroke_fill="#000a", anchor="mm")
        draw_screen_text(overlay, layout, options)
        path = folder / f"clip-title-{layout}.png"
        overlay.save(path)
        inputs += ["-loop", "1", "-framerate", "24", "-i", str(path)]
        filters += ";[clipbg][1:v]overlay=0:0:shortest=1[background]"
        return inputs, filters, 2
    return inputs, filters + ";[clipbg]null[background]", 1
