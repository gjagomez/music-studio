"""Validate editable LRC timings and render them safely through an ASS subtitle file."""
import re


def parse_lrc(text):
    cues = []
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"\[(\d{1,2}):([0-5]\d)(?:\.(\d{1,3}))?\](.{1,200})", line.strip())
        if not match:
            raise ValueError('Cada frase necesita un tiempo: [00:12.50]Texto de la frase.')
        minute, second, fraction, words = match.groups()
        start = int(minute)*60 + int(second) + int((fraction or '0').ljust(3, '0'))/1000
        if start > 600 or (cues and start <= cues[-1]['start']):
            raise ValueError('Los tiempos deben aumentar, sin repetirse, y quedar antes de 10 minutos.')
        cues.append({'start': start, 'text': words.strip()})
    if len(cues) > 150:
        raise ValueError('Podés sincronizar hasta 150 frases por video.')
    return cues


def timed_overlay_input(folder, lrc, start, end, layout, color='#ffffff'):
    """Build one transparent timed video input using PNG cues; no libass dependency."""
    from PIL import Image
    from video_renderer import SIZES, draw_screen_text
    cues = parse_lrc(lrc)
    width, height = SIZES[layout]
    blank_name = f'lyric-{layout}-blank.png'
    Image.new('RGBA', (width, height)).save(folder/blank_name)
    segments, cursor = [], 0.0
    for i, cue in enumerate(cues):
        begin = max(cue['start'], start)-start
        finish = min(cues[i+1]['start'] if i+1 < len(cues) else end, end)-start
        if finish <= begin:
            continue
        if begin > cursor:
            segments.append((blank_name, begin-cursor))
        name = f'lyric-{layout}-{i:03}.png'
        image = Image.new('RGBA', (width, height))
        draw_screen_text(image, layout, {'screen_text': cue['text'], 'text_position': 'bottom',
                                        'text_size': 'medium', 'text_color': color})
        image.save(folder/name)
        segments.append((name, finish-begin))
        cursor = finish
    if cursor < end-start:
        segments.append((blank_name, end-start-cursor))
    if not segments:
        segments = [(blank_name, end-start)]
    concat = folder/f'lyrics-{layout}.txt'
    lines = []
    for name, duration in segments:
        lines.extend([f"file '{name}'", f'duration {duration:.6f}'])
    lines.append(f"file '{segments[-1][0]}'")
    concat.write_text('\n'.join(lines)+'\n')
    return ['-f', 'concat', '-safe', '0', '-i', str(concat)]
