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


LIFE_STYLE = ("Candid smartphone photo taken at home in daylight, realistic like a real photo from a family phone, not an ad. "
              "COMPOSITION MUST BE SIMPLE: one or two people at most, medium shot, a clear readable pose, nothing tricky — "
              "no cars, no crowds, no mirrors, no people partly hidden behind furniture, no overlapping bodies. "
              "ANATOMY MUST BE PERFECT: every person has exactly two arms and two hands with five fingers; hands are relaxed "
              "or simply holding one object (phone, mug, notebook); people sit firmly on a chair or sofa, stand on the floor. "
              "One continuous room with consistent walls, ceiling and perspective. "
              "EMOTIONS ARE NATURAL AND FIT THE SCENE: calm, thinking, a bit tired, focused, amused by a message, a small "
              "warm smile — never a fixed toothy smile at nothing, never posing for the camera, people look at what they do. "
              "Light: soft natural daylight, warm and fresh, not gloomy. "
              "Setting: a modern, recently renovated apartment of a middle-class family in a Russian city in 2026 — "
              "light painted walls, laminate floor, contemporary kitchen with built-in appliances, neat modern furniture "
              "(IKEA/Hoff style), indoor plants, tidy but lived-in; not shabby, not old Soviet, not luxury. "
              "The mom is a well-kept woman in her early 40s in casual modern clothes. Teenagers (14–15, girl or boy as the "
              "scene says) dress like Russian teens in 2026: oversized hoodie in a trendy color, wide-leg jeans or cargo "
              "pants, chunky sneakers, headphones; teenagers never show their face: back view, turned away or hair/headphones "
              "covering it. No text, no letters, no logos, no watermarks.")


def life(scene: str, aspect: str = "9:16", prev: bytes = None) -> bytes:
    """Картинка «из жизни» для мам 35–50: их дом, их ребёнок, их эмоция.
    prev — первый кадр того же ролика: та же семья, та же квартира, тот же свет."""
    parts = []
    note = ""
    if prev:
        parts.append({"inlineData": {"mimeType": "image/png", "data": base64.b64encode(prev).decode()}})
        note = ("The attached image is an earlier shot of the same video: keep the same family members (same mom, same "
                "teen's clothes and hair), the same apartment style and the same light. ")
    prompt = (f"{note}Scene: {scene}\nVertical {aspect} composition; the top third of the frame is calm (wall, "
              f"ceiling or window) because chat bubbles will cover it; people and action in the lower two thirds. "
              f"{LIFE_STYLE}")
    parts.append({"text": prompt})
    return _call(parts, aspect)


def life_checked(scene: str, aspect: str = "9:16", tries: int = 3):
    """Кадр «из жизни» с проверкой: рисуем до tries вариантов, строгий проверяющий ищет брак (лишние руки, люди
    внутри мебели, чужой потолок, натянутые улыбки). Возвращает (картинка, отчёт) лучшего варианта
    или (None, отчёт), если брак во всех."""
    from . import text
    best, best_rep = None, None
    for _ in range(tries):
        try:
            img = life(scene, aspect)
        except Exception as e:  # noqa: BLE001
            best_rep = best_rep or {"ok": False, "score": 0, "problems": [str(e)[:200]]}
            continue
        rep = text.check_photo(img, scene)
        if rep["ok"]:
            return img, rep
        if not best_rep or rep["score"] > best_rep.get("score", 0):
            best, best_rep = img, rep
    return None, best_rep


def without_people(scene: str) -> str:
    return ("The same everyday place as in this description, but WITHOUT any people, hands or faces — only the room and "
            f"the objects that tell the story (headphones on the desk, a mug, an open laptop, a school backpack): {scene}")
