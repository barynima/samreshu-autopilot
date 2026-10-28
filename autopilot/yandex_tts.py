"""Голос мамы через Yandex SpeechKit (API v3, REST). Нужен секрет YANDEX_TTS_KEY — API-ключ сервисного аккаунта
с ролью ai.speechkit-tts.user. Голос и амплуа задаются переменными YANDEX_TTS_VOICE / YANDEX_TTS_ROLE /
YANDEX_TTS_MODEL (general или livetts) / YANDEX_TTS_SPEED. Если ключа нет — бот озвучивает маму через Google, как раньше."""
import base64
import io
import json
import os
import wave

import requests

URL = "https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis"
RATE = 48000

# Кандидаты для пробника: (модель, голос, амплуа)
SAMPLES = [
    ("general", "masha", "friendly"),
    ("general", "masha", "good"),
    ("general", "dasha", "friendly"),
    ("general", "dasha", "good"),
    ("general", "lera", "friendly"),
    ("general", "marina", "friendly"),
    ("general", "jane", "good"),
    ("livetts", "sofia", "casual"),
    ("livetts", "vera", "casual"),
]


def _env(k: str, d: str = "") -> str:
    return (os.environ.get(k) or d).strip()


def enabled() -> bool:
    return bool(_env("YANDEX_TTS_KEY"))


def label() -> str:
    return f"{_env('YANDEX_TTS_VOICE', 'masha')}/{_env('YANDEX_TTS_ROLE', 'friendly')}"


def _request(text: str, voice: str, role: str, model: str, speed: float, spec: dict) -> bytes:
    hints = [{"voice": voice}]
    if role:
        hints.append({"role": role})
    if speed and abs(speed - 1.0) > 0.01:
        hints.append({"speed": round(speed, 2)})
    body = {"text": text, "hints": hints, "outputAudioSpec": spec, "loudnessNormalizationType": "LUFS"}
    if model and model != "general":
        body["model"] = model
    r = requests.post(URL, headers={"Authorization": f"Api-Key {_env('YANDEX_TTS_KEY')}"},
                      data=json.dumps(body).encode(), timeout=90)
    if r.status_code != 200:
        raise RuntimeError(f"Яндекс {voice}/{role}: HTTP {r.status_code} {r.text[:600]}")
    audio = b""
    for line in r.text.splitlines():  # ответ — несколько JSON подряд, в каждом кусок звука
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "error" in msg:
            raise RuntimeError(f"Яндекс {voice}/{role}: {str(msg['error'])[:200]}")
        chunk = ((msg.get("result") or msg).get("audioChunk") or {}).get("data")
        if chunk:
            audio += base64.b64decode(chunk)
    if not audio:
        raise RuntimeError(f"Яндекс {voice}/{role}: пустой ответ")
    return audio


def synth(text: str, voice: str = "", role: str = "", model: str = "", speed: float = 0.0) -> bytes:
    """Возвращает WAV 48 кГц, моно, 16 бит — так же, как Google, чтобы сборка ролика не менялась."""
    voice = voice or _env("YANDEX_TTS_VOICE", "masha")
    role = role if role else _env("YANDEX_TTS_ROLE", "friendly")
    model = model or _env("YANDEX_TTS_MODEL", "general")
    speed = speed or float(_env("YANDEX_TTS_SPEED", "1.0") or 1.0)
    pcm = _request(text, voice, role, model, speed,
                   {"rawAudio": {"audioEncoding": "LINEAR16_PCM", "sampleRateHertz": RATE}})
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def sample_ogg(text: str, model: str, voice: str, role: str) -> bytes:
    """Пробник для Telegram — голосовое сообщение (OGG Opus)."""
    return _request(text, voice, role, model, 1.0, {"containerAudio": {"containerAudioType": "OGG_OPUS"}})
