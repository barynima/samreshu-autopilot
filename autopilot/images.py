"""Картинки через Gemini (Google AI Studio). Эталоны персонажей лежат в refs/sam.png и refs/nado.png."""
import base64
import os
from pathlib import Path

import requests

from .characters import NADO, SAM, STYLE

ROOT = Path(__file__).resolve().parent.parent
REFS = ROOT / "refs"


def _call(parts: list, aspect: str) -> bytes:
    model = os.environ.get("IMAGE_MODEL") or "gemini-3-pro-image"
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": os.environ["GEMINI_API_KEY"]},
        json={
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": {"aspectRatio": aspect},
            },
        },
        timeout=300,
    )
    r.raise_for_status()
    for cand in r.json().get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            data = part.get("inlineData") or part.get("inline_data")
            if data:
                return base64.b64decode(data["data"])
    raise RuntimeError(f"Gemini не вернул картинку: {r.text[:500]}")


def _ref_part(name: str):
    path = REFS / f"{name}.png"
    if not path.exists():
        return None
    return {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(path.read_bytes()).decode()}}


def reference_sheet(prompt: str) -> bytes:
    return _call([{"text": prompt}], "1:1")


def panel(scene: str) -> bytes:
    parts, notes = [], []
    for name, label in (("sam", "Sam (small kid in coral hoodie)"), ("nado", "Nado (grey-blue cloud)")):
        ref = _ref_part(name)
        if ref:
            parts.append(ref)
            notes.append(label)
    keep = (
        f"Use the attached images as character references for {' and '.join(notes)}; "
        "keep their look exactly the same. " if notes else ""
    )
    prompt = (
        f"{keep}Characters: {SAM} {NADO}\nScene: {scene}\n"
        f"Vertical 4:5 composition, leave the top quarter calm and empty for a caption. {STYLE}"
    )
    parts.append({"text": prompt})
    return _call(parts, "4:5")
