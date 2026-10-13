"""Картинки через Gemini (Vertex AI, как в боте для интерьеров). Эталоны персонажей лежат в refs/sam.png и refs/nado.png."""
import base64
import os
from pathlib import Path

from . import google
from .characters import NADO, SAM, STYLE

ROOT = Path(__file__).resolve().parent.parent
REFS = ROOT / "refs"


def _call(parts: list, aspect: str) -> bytes:
    model = os.environ.get("IMAGE_MODEL") or "gemini-3-pro-image"
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": {"aspectRatio": aspect}},
    }
    try:
        data = google.generate(model, payload)
    except RuntimeError as e:
        if "imageConfig" not in str(e) and "aspect" not in str(e).lower():
            raise
        payload["generationConfig"].pop("imageConfig")  # модель не знает про пропорции — обрежем сами
        data = google.generate(model, payload)
    for cand in data.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("mimeType", "image/").startswith("image/"):
                return base64.b64decode(inline["data"])
    raise RuntimeError(f"Gemini не вернул картинку: {str(data)[:400]}")


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
