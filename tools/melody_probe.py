"""Local monophonic pitch prototype: WAV humming -> note timings, MIDI and synthesized preview.
Run with ACE-Step's Python environment (numpy and scipy); this does not run a generation model.
"""
import argparse
import json
import struct
import wave
from pathlib import Path
import numpy as np
from scipy.signal import resample_poly, medfilt


def transcribe(samples, rate):
    audio = resample_poly(samples, 16000, rate)
    size, hop = 2048, 160
    low, high = 20, 200  # 800 to 80 Hz
    pitches = []
    levels = []
    for start in range(0, max(0, len(audio) - size + 1), hop):
        frame = audio[start:start + size].astype(float)
        frame -= frame.mean()
        rms = np.sqrt(np.mean(frame * frame))
        levels.append(rms)
        if rms < .004:
            pitches.append(0)
            continue
        # YIN cumulative mean normalized difference, using a fixed overlap.
        differences = np.zeros(high + 1)
        for lag in range(1, high + 1):
            differences[lag] = np.sum((frame[:size-high] - frame[lag:size-high+lag]) ** 2)
        cumulative = np.cumsum(differences[1:])
        normalized = np.ones(high + 1)
        normalized[1:] = differences[1:] * np.arange(1, high + 1) / np.maximum(cumulative, 1e-12)
        lag = low
        while lag < high and normalized[lag] >= .15:
            lag += 1
        if lag == high:
            lag = low + int(np.argmin(normalized[low:high+1]))
        else:
            while lag < high and normalized[lag+1] < normalized[lag]:
                lag += 1
        if normalized[lag] > .3:
            pitches.append(0)
            continue
        refined = float(lag)
        if low < lag < high:
            a, b, c = normalized[lag-1:lag+2]
            denominator = a - 2*b + c
            if abs(denominator) > 1e-9:
                refined += .5 * (a-c) / denominator
        pitches.append(int(round(69 + 12*np.log2((16000/refined)/440))))
    if not pitches:
        return [], 0
    smooth = medfilt(np.array(pitches), kernel_size=5)
    # Reject short note fragments instead of inventing notes in uncertain areas.
    notes = []
    begin = 0
    for i in range(1, len(smooth)+1):
        if i == len(smooth) or smooth[i] != smooth[begin]:
            note = int(smooth[begin])
            if note and i-begin >= 8:
                start, end = begin*.01 + .032, i*.01 + .032
                if notes and notes[-1]['midi'] == note and start-notes[-1]['end'] <= .08:
                    notes[-1]['end'] = round(end, 3)
                else:
                    notes.append({'midi': note, 'start': round(start, 3), 'end': round(end, 3)})
            begin = i
    return notes, round(float(np.count_nonzero(smooth) / len(smooth)), 3)


def variable(value):
    result = [value & 127]
    while value >> 7:
        value >>= 7
        result.insert(0, (value & 127) | 128)
    return bytes(result)


def export(notes, duration, target):
    target.mkdir(parents=True, exist_ok=True)
    rate = 32000
    audio = np.zeros(int((duration + .5)*rate))
    events = []
    for note in notes:
        start, end, midi = note['start'], note['end'], note['midi']
        t = np.arange(int((end-start+.15)*rate)) / rate
        freq = 440 * 2**((midi-69)/12)
        tone = sum(np.sin(2*np.pi*freq*h*t) * (1/h**1.7) * np.exp(-t*(1.8+h*.3)) for h in range(1, 6))
        envelope = np.minimum(t/.012, 1) * np.minimum(np.maximum(end-start+.15-t, 0)/.15, 1)
        offset = int(start*rate)
        length = min(len(tone), len(audio)-offset)
        audio[offset:offset+length] += tone[:length] * envelope[:length]
        events.extend([(round(start*960), bytes([0x90, midi, 90])), (round(end*960), bytes([0x80, midi, 0]))])
    peak = max(float(np.max(np.abs(audio))), 1e-9)
    with wave.open(str(target/'melodia-piano.wav'), 'wb') as f:
        f.setparams((1, 2, rate, 0, 'NONE', 'NONE'))
        f.writeframes((audio/peak*.65*32767).astype('<i2').tobytes())
    track = b'\x00\xff\x51\x03\x07\xa1\x20' + b'\x00\xc0\x00'
    previous = 0
    for tick, data in sorted(events, key=lambda e: (e[0], e[1][0])):
        track += variable(tick-previous) + data
        previous = tick
    track += b'\x00\xff\x2f\x00'
    (target/'melodia.mid').write_bytes(b'MThd'+struct.pack('>IHHH',6,0,1,480)+b'MTrk'+struct.pack('>I',len(track))+track)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    with wave.open(str(args.source)) as f:
        if f.getsampwidth() != 2:
            raise ValueError('Se necesita WAV PCM de 16 bits.')
        rate, channels = f.getframerate(), f.getnchannels()
        samples = np.frombuffer(f.readframes(f.getnframes()), dtype='<i2').astype(float).reshape(-1, channels).mean(axis=1)/32768
    notes, coverage = transcribe(samples, rate)
    duration = len(samples)/rate
    if not notes:
        raise SystemExit('No se detectaron notas estables. No se generó una melodía inventada.')
    export(notes, duration, args.output)
    report = {'source': args.source.name, 'duration': duration, 'voiced_frame_ratio': coverage, 'notes': notes, 'experimental': True}
    (args.output/'notas.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'duration': duration, 'notes': len(notes), 'voiced_frame_ratio': coverage, 'output': str(args.output)}))


if __name__ == '__main__':
    main()
