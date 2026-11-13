"""Speak each line of docs/video/narration.json with a local Kokoro voice (no API).

    KOKORO_DIR=~/.cache/kk  <venv-with-kokoro-onnx>/bin/python scripts/tts.py
Writes docs/video/voice/<key>.wav and docs/video/voice/durations.json. Unchanged lines are reused.
"""
import hashlib
import json
import os
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

ROOT = Path(__file__).resolve().parent.parent
VO = ROOT / "docs" / "video" / "voice"
KD = Path(os.path.expanduser(os.environ.get("KOKORO_DIR", "~/.cache/kk")))
VOICE = os.environ.get("KOKORO_VOICE", "af_heart")


def main():
    lines = json.loads((ROOT / "docs" / "video" / "narration.json").read_text())
    VO.mkdir(parents=True, exist_ok=True)
    meta_p = VO / "durations.json"
    meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
    k = None
    for key, text in lines.items():
        h = hashlib.md5(f"{VOICE}|{text}".encode()).hexdigest()[:10]
        wav = VO / f"{key}.wav"
        if meta.get(key, {}).get("hash") == h and wav.exists():
            continue
        k = k or Kokoro(str(KD / "kokoro-v1.0.onnx"), str(KD / "voices-v1.0.bin"))
        audio, sr = k.create(text, voice=VOICE, speed=1.0, lang="en-us")
        sf.write(wav, audio, sr)
        meta[key] = {"hash": h, "seconds": round(len(audio) / sr, 3)}
        print(key, meta[key]["seconds"])
    meta = {k2: v for k2, v in meta.items() if k2 in lines}
    meta_p.write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
