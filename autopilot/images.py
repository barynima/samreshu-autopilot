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


def frame(scene: str, aspect: str = "4:5", layout: str = "leave the top quarter calm and empty for a caption",
          prev: bytes = None) -> bytes:
    """prev — первый кадр ролика: следующие кадры рисуем в той же комнате, с тем же светом и ракурсом."""
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
    if prev:
        parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(prev).decode()}})
        keep += ("The LAST attached image is the previous shot of the same video: keep exactly the same room, "
                 "furniture, background, lighting, colors and camera distance; change only the characters' "
                 "poses, sizes and emotions as the scene says. ")
    orientation = "Vertical 9:16" if aspect == "9:16" else f"Vertical {aspect}"
    prompt = (
        f"{keep}Characters (draw only those the scene mentions): {SAM} {NADO}\nScene: {scene}\n"
        f"{orientation} composition, {layout}. {STYLE}"
    )
    parts.append({"text": prompt})
    return _call(parts, aspect)


def panel(scene: str) -> bytes:
    return frame(scene, "4:5")


def still_life(scene: str) -> bytes:
    """Фон для текстового Reels: обычная домашняя деталь без людей и персонажей."""
    prompt = (f"Scene: {scene}\nVertical 9:16 photo. Warm, cozy, realistic home atmosphere, soft natural light, "
              "shallow depth of field, calm muted colors, slightly blurred so big text can sit on top. "
              "No people, no hands, no faces, no characters, no text, no letters, no logos.")
    return _call([{"text": prompt}], "9:16")


LIFE_STYLE = ("Candid smartphone photo taken by a mom at home: natural warm light, slight grain, realistic, real emotions. "
              "Setting: a modern, recently renovated apartment of a middle-class family in a Russian city in 2026 — "
              "new-build flat, light painted walls, laminate floor, contemporary kitchen with built-in appliances and a "
              "light countertop, neat modern furniture (IKEA/Hoff style), indoor plants, tidy but lived-in. "
              "NOT shabby, NOT old Soviet: no peeling wallpaper, no carpets on walls, no old furniture, no clutter or dirt; "
              "NOT luxury either. People look well-groomed in casual modern clothes; the mom is an attractive "
              "well-kept woman in her early 40s. Teenagers (14–15) dress like real Russian teens in 2026: oversized "
              "hoodie or zip-hoodie in a trendy color (sage, beige, black, washed lilac) or with a small graphic, baggy "
              "wide-leg jeans or cargo pants, chunky sneakers, wireless over-ear headphones or earbuds, modern smartphone; "
              "styled hair (fluffy, curtain bangs), hood usually down. Teenagers never show their face: back view, "
              "turned away, hair or headphones covering it, or out of frame. A teen room may be a bit messy "
              "(unmade bed, clothes on a chair) — that is real life — but the room itself is modern. No text, no letters, no logos, no watermarks.")


def life(scene: str, aspect: str = "9:16") -> bytes:
    """Картинка «из жизни» для мам 35–50: их дом, их ребёнок, их эмоция."""
    prompt = (f"Scene: {scene}\nVertical {aspect} composition, keep the top quarter calm (wall or ceiling) "
              f"for a caption. {LIFE_STYLE}")
    return _call([{"text": prompt}], aspect)
