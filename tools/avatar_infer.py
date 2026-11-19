"""Bounded, streaming MuseTalk inference using the separately installed Mac engine."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def progress(percent, message):
    print('ALMA_PROGRESS '+json.dumps({'percent': percent, 'message': message}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', choices=['mps', 'cpu'], default='mps')
    args = parser.parse_args()
    sys.path.insert(0, str(args.engine))
    os.chdir(args.engine)
    import cv2
    import numpy as np
    import torch
    from transformers import WhisperModel
    from musetalk.models.vae import VAE
    from musetalk.models.unet import UNet, PositionalEncoding
    from musetalk.utils.audio_processor import AudioProcessor
    from musetalk.utils.preprocessing import get_landmark_and_bbox, coord_placeholder
    from musetalk.utils.blending import get_image
    import musetalk.utils.face_parsing as parsing

    torch.set_num_threads(4)
    torch.manual_seed(42)
    device = torch.device('mps' if args.device == 'mps' and torch.backends.mps.is_available() else 'cpu')
    dtype = torch.float16 if device.type == 'mps' else torch.float32
    progress(8, 'Preparando el rostro…')
    if args.source.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'}:
        inputs = [str(args.source)]
    else:
        capture = cv2.VideoCapture(str(args.source))
        work = args.output.parent/'source_frames'
        work.mkdir(exist_ok=True)
        inputs = []
        for i in range(75):
            ok, frame = capture.read()
            if not ok:
                break
            path = work/f'{i:04}.png'
            cv2.imwrite(str(path), frame)
            inputs.append(str(path))
        capture.release()
    if not inputs:
        raise RuntimeError('No se pudo leer la foto o el video.')
    coords, frames = get_landmark_and_bbox(inputs, 0)
    if not coords or any(c == coord_placeholder for c in coords):
        raise RuntimeError('No se detectó un rostro claro. Usá una cara de frente, bien iluminada y sin tapar la boca.')
    progress(15, 'Cargando el motor de labios…')
    with torch.inference_mode():
        vae = VAE(model_path='models/sd-vae')
        vae.vae = vae.vae.to(device=device, dtype=dtype).eval()
        unet = UNet(unet_config='models/musetalkV15/musetalk.json', model_path='models/musetalkV15/unet.pth', device='cpu')
        unet.model = unet.model.to(device=device, dtype=dtype).eval()
        pe = PositionalEncoding(384).to(device=device, dtype=dtype)
        whisper = WhisperModel.from_pretrained('models/whisper', local_files_only=True).to(device=device, dtype=dtype).eval()
        parsing._pick_device = lambda: device
        face_parser = parsing.FaceParsing()
        masks = {}
        def cached_mask(image, mode='raw'):
            key = (mode, image.size, hash(image.tobytes()))
            if key not in masks:
                if len(masks) >= 100:
                    masks.clear()
                masks[key] = face_parser(image, mode=mode)
            return masks[key].copy()
        progress(22, 'Analizando la voz de la canción…')
        processor = AudioProcessor('models/whisper')
        features, length = processor.get_audio_feature(str(args.audio))
        chunks = processor.get_whisper_chunk(features, device, dtype, whisper, length, fps=15)
        latents, boxes = [], []
        for box, frame in zip(coords, frames):
            x1, y1, x2, y2 = map(int, box)
            y2 = min(y2+10, frame.shape[0])
            if x2 <= x1 or y2 <= y1:
                raise RuntimeError('El rostro está demasiado cerca del borde. Probá otra foto.')
            boxes.append((x1, y1, x2, y2))
            crop = cv2.resize(frame[y1:y2, x1:x2], (256, 256), interpolation=cv2.INTER_LANCZOS4)
            latents.append(vae.get_latents_for_unet(crop).to(device=device, dtype=dtype))
        del whisper, features, processor
        if device.type == 'mps':
            torch.mps.empty_cache()
        height, width = frames[0].shape[:2]
        args.output.parent.mkdir(parents=True, exist_ok=True)
        encoder = subprocess.Popen(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24',
                                    '-s', f'{width}x{height}', '-r', '15', '-i', 'pipe:0', '-an',
                                    '-vf', 'pad=ceil(iw/2)*2:ceil(ih/2)*2', '-c:v', 'libx264',
                                    '-preset', 'veryfast', '-threads', '2', '-pix_fmt', 'yuv420p', str(args.output)], stdin=subprocess.PIPE)
        try:
            total = len(chunks)
            for i in range(total):
                phase = i % (2*len(frames))
                index = phase if phase < len(frames) else 2*len(frames)-phase-1
                prediction = unet.model(latents[index], torch.tensor([0], device=device), encoder_hidden_states=pe(chunks[i:i+1].to(device=device, dtype=dtype))).sample
                face = vae.decode_latents(prediction)[0]
                x1, y1, x2, y2 = boxes[index]
                face = cv2.resize(face.astype(np.uint8), (x2-x1, y2-y1))
                blended = get_image(frames[index], face, boxes[index], mode='jaw', fp=cached_mask)
                encoder.stdin.write(np.ascontiguousarray(blended).tobytes())
                if i % 5 == 0 or i+1 == total:
                    progress(25+int((i+1)/total*65), f'Animando la boca · {i+1} de {total} cuadros')
            encoder.stdin.close()
            if encoder.wait(timeout=60):
                raise RuntimeError('No se pudo guardar la animación.')
        finally:
            if encoder.poll() is None:
                encoder.kill()
                encoder.wait()
    progress(92, 'Animación terminada. Preparando el video final…')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ALMA_ERROR '+str(error), flush=True)
        raise
