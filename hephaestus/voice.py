"""Muse Voice Transcribe: a recorded WAV clip -> text, for voice edits.

Docs: dev.meta.ai/docs/speech-to-text and /docs/api-reference/voice/transcribe.
- POST https://api.meta.ai/v1/asr/transcribe, multipart: `request` (JSON: model, audioEncoding "WAV", mode, keywords)
  and `audio` (RIFF/WAVE, mono, 16-bit PCM, 16 or 24 kHz). Other formats must be converted first: the browser
  records WebM/Opus and re-encodes to 16 kHz WAV before upload (web/app.js).
- Response: {sessionId, transcript, audioDurationMs, turns}. Price: $0.18 per hour of audio, billed per whole second.
"""
import io
import json
import os
import time
import wave

import requests

from . import meta_api

URL = "https://api.meta.ai/v1/asr/transcribe"
MODEL = "muse-voice-transcribe-1.0"
PRICE_PER_SECOND = 0.18 / 3600
MAX_SECONDS = 30            # an edit instruction is a sentence; longer clips are refused before upload
# terms an edit uses that a general model might mishear ("M6" as "M sex", "fillet" as "filet")
KEYWORDS = ["M3", "M4", "M5", "M6", "M8", "M10", "fillet", "chamfer", "counterbore", "countersink", "slot",
            "pocket", "millimetres", "mm", "diameter", "radius", "thickness", "upright"]


class VoiceError(RuntimeError):
    pass


def wav_info(data):
    """(seconds, rate, channels, sample width) of a WAV clip; raises VoiceError if it is not one the API takes."""
    try:
        with wave.open(io.BytesIO(data)) as w:
            rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
    except (wave.Error, EOFError) as e:
        raise VoiceError(f"not a WAV file ({e})") from None
    if ch != 1 or width != 2 or rate not in (16000, 24000):
        raise VoiceError(f"the clip must be mono 16-bit PCM at 16 or 24 kHz (got {ch} channel(s), "
                         f"{8 * width}-bit, {rate} Hz)")
    seconds = n / rate
    if seconds > MAX_SECONDS:
        raise VoiceError(f"the clip is {seconds:.0f} s long; keep an edit under {MAX_SECONDS} s")
    if seconds < 0.3:
        raise VoiceError("the clip is empty")
    return seconds, rate, ch, width


def transcribe(data, budget=None, tag=None):
    """Transcribe a WAV clip. Returns {transcript, audio_ms, seconds, cost_usd}."""
    wav_info(data)
    if budget is not None:
        budget.check()
    req = {"model": MODEL, "audioEncoding": "WAV", "mode": "PUSH_TO_TALK", "keywords": KEYWORDS,
           "languageBias": ["English"]}
    key = os.environ.get("MODEL_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    t0, last = time.time(), None
    for attempt in range(3):
        try:
            r = requests.post(URL, headers=headers, timeout=(10, 60),
                              files={"request": (None, json.dumps(req), "application/json"),
                                     "audio": ("clip.wav", data, "audio/wav")})
            if r.status_code == 200:
                out = r.json()
                break
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in (429, 500, 502, 503):
                raise VoiceError(last)
        except requests.RequestException as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(2 * (attempt + 1))
    else:
        raise VoiceError(last)
    audio_ms = int(out.get("audioDurationMs") or 0)
    cost = (audio_ms // 1000) * PRICE_PER_SECOND
    if budget is not None:
        budget.charge(cost)
    meta_api.log_spend(MODEL, {"input_tokens": 0, "output_tokens": 0}, cost, f"transcribe {audio_ms} ms", tag)
    return {"transcript": (out.get("transcript") or "").strip(), "audio_ms": audio_ms,
            "seconds": round(time.time() - t0, 2), "cost_usd": round(cost, 6)}
