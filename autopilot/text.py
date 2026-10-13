"""Сценарий эпизода: 4 кадра + подпись к посту.

Пишет Claude через Cloud.ru (OpenAI-совместимый API). Если ключей Cloud.ru нет —
пишет Gemini через Vertex AI тем же ключом, что и картинки.
"""
import json
import os
import re

import requests

from . import google
from .characters import PASSPORT

SYSTEM = f"""Ты — сценарист Instagram-аккаунта бренда «Сам решу».
{PASSPORT}

Тебе дают идею эпизода. Сделай комикс из 4 кадров и подпись к посту.
Реплики живые и короткие: максимум 90 символов на кадр, говорит один персонаж.
Каждый кадр — одна сцена, понятная без текста. Финальный кадр и подпись — с крючком.

Ответь ТОЛЬКО JSON без пояснений:
{{
  "panels": [
    {{"speaker": "Надо" | "Сам", "line": "реплика по-русски",
      "scene": "image prompt in ENGLISH: who is where, poses, Nado's size (huge/medium/small), emotions, background"}}
    ... ровно 4 кадра
  ],
  "caption": "подпись к посту по-русски: 2–5 коротких абзацев, без канцелярита, в конце крючок, затем строка
              'Пройти «Сам решу» — ссылка в профиле' и 5–8 хештегов (#самрешу #профориентация #огэ ...)"
}}"""


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"Модель не вернула JSON: {text[:300]}")
    data = json.loads(match.group(0))
    panels = data.get("panels") or []
    if len(panels) != 4 or not data.get("caption"):
        raise ValueError(f"Неполный сценарий: {data}")
    for p in panels:
        if p.get("speaker") not in ("Сам", "Надо") or not p.get("line") or not p.get("scene"):
            raise ValueError(f"Плохой кадр: {p}")
    return data


def _cloudru(user: str) -> str:
    base = os.environ["CLOUDRU_BASE_URL"].rstrip("/")
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['CLOUDRU_API_KEY']}"},
        json={
            "model": os.environ["TEXT_MODEL"],
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "temperature": 0.9,
            "max_tokens": 2000,
        },
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _gemini(user: str) -> str:
    model = os.environ.get("GEMINI_TEXT_MODEL") or "gemini-2.5-pro"
    data = google.generate(model, {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"},
    }, timeout=180)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def write_episode(idea: str, audience: str, feedback: str = "") -> dict:
    who = "для родителей школьников" if audience == "parent" else "для школьников 8–9 класса"
    user = f"Эпизод {who}.\nИдея: {idea.strip()}"
    if feedback:
        user += f"\n\nПрошлый вариант не понравился. Комментарий: {feedback}"
    use_cloudru = all(os.environ.get(k) for k in ("CLOUDRU_BASE_URL", "CLOUDRU_API_KEY", "TEXT_MODEL"))
    last_err = None
    for _ in range(3):
        try:
            raw = _cloudru(user) if use_cloudru else _gemini(user)
            return _extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
    raise RuntimeError(f"Не получилось написать эпизод: {last_err}")
