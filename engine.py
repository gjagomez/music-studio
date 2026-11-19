"""Connect the studio to the local ACE-Step Gradio service."""
import secrets
import shutil
from pathlib import Path

from gradio_client import Client, handle_file

from styles import PRESETS, MOODS


def generate(song: dict, folder: Path, progress) -> Path:
    """Generate one song and copy the resulting MP3 into its library folder."""
    progress("Preparando el motor…")
    client = Client("http://127.0.0.1:7860", verbose=False)
    schema = client.view_api(return_format="dict", print_info=False)
    fields = schema["named_endpoints"]["/generation_wrapper"]["parameters"]
    args = {p["parameter_name"]: p["parameter_default"] for p in fields}
    language = song.get("language", "es")
    instrumental = song.get("instrumental", False) or song["style"] == "instrumental"
    voice = {"male": "one natural male baritone lead voice", "female": "one natural female lead voice"}[song["voice"]]
    caption = ". ".join(part for part in [PRESETS[song["style"]], song["description"], MOODS.get(song.get("mood", "sincero"), MOODS["sincero"])] if part)
    if language == "en":
        caption = caption.replace("Spanish language pop", "English language pop").replace("rock en espanol", "English-language rock")
    if instrumental:
        caption += ". Instrumental only, no vocals, no singing"
    else:
        caption += ". Clear English diction, sung in English" if language == "en" else ". Clear Spanish diction, sung in Spanish"
    if not instrumental:
        caption += ". " + voice + ", relaxed phrasing, subtle vibrato, breaths between phrases, upfront dry vocal"
    args.update({
        "param_0": caption, "param_1": "[Instrumental]" if instrumental else song["lyrics"],
        "param_2": song["bpm"], "param_3": "", "param_4": "4", "param_5": "unknown" if instrumental else language,
        "param_6": 4 if song.get("quality") == "fast" else 8,
        "param_8": False, "param_9": str(song["seed"] if song.get("seed") is not None else secrets.randbelow(2**31)), "param_10": None,
        "param_11": song["duration"], "param_12": 1, "param_13": None, "param_14": "",
        "param_36": "mp3", "param_37": "320k", "param_38": 44100, "param_40": False,
        "param_45": False, "param_46": False, "param_47": False,
        "param_55": None, "param_59": 0.15, "param_60": 1.5,
        "param_68": "", "param_69": "", "param_73": False,
    })
    if song.get("melody_id"):
        melody_id = song["melody_id"]
        if len(melody_id) != 32 or any(c not in "0123456789abcdef" for c in melody_id):
            raise RuntimeError("La melodía no es válida.")
        source_melody = folder.parent / "_melodies" / (melody_id + ".wav")
        if not source_melody.is_file():
            raise RuntimeError("Volvé a subir o grabar tu melodía.")
        # Preserve the source alongside the resulting song for later comparisons.
        shutil.copy2(source_melody, folder / "melody.wav")
        # task_type is hidden Gradio session state in this engine revision.
        # Set it through the mode handler using the same Client/session.
        if "param_20" not in args:
            if "/_handle_mode_change" not in schema["named_endpoints"]:
                raise RuntimeError("Este motor no expone el modo melodía. Revisá la versión de ACE-Step.")
            client.predict(mode="Remix", api_name="/_handle_mode_change")
        args["param_0"] += ". Preserve the source melody, pitch contour and rhythm"
        args["param_11"] = song.get("melody_duration", song["duration"])
        args.update({"param_13": handle_file(str(folder / "melody.wav")),
                     "param_17": "Generate audio semantic tokens based on the given conditions:",
                     "param_21": False,
                     "param_19": song.get("melody_strength", 0.6),
                     "param_18": 1.0})
        if "param_20" in args:
            args["param_20"] = "cover"
    progress("Creando tu canción completa… Puede tardar varios minutos." if song["duration"] >= 60 else "Creando tu canción… Puede tardar unos minutos.")
    result = client.predict(**args, api_name="/generation_wrapper")
    source = result[0]
    if isinstance(source, dict):
        source = source.get("path") or source.get("value")
    if not source and result[8]:
        source = next((p for p in result[8] if str(p).endswith(".mp3")), None)
    if not source or not Path(source).is_file():
        status = str(result[10]) if len(result) > 10 else "Sin audio"
        if "out of memory" in status.lower():
            raise RuntimeError("No alcanzó la memoria. Reiniciá ACE-Step e intentá con 15 segundos.")
        raise RuntimeError("ACE-Step no devolvió audio. Revisá que el motor esté listo y volvé a intentar.")
    target = folder / "audio.mp3"
    shutil.copy2(source, target)
    return target
