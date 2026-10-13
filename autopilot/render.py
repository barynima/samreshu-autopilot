"""Накладывает реплику на кадр: Instagram-формат 1080×1350, JPEG."""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
CORAL = (255, 122, 89)
SLATE = (110, 120, 140)
INK = (40, 40, 48)

FONT_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "fonts" / "Font-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
]


def _font(size: int):
    for p in FONT_CANDIDATES:
        if p.exists():
            return ImageFont.truetype(str(p), size)
    raise RuntimeError("Не найден шрифт: положите .ttf в fonts/Font-Bold.ttf")


def _fit_cover(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    scale = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((left, top, left + W, top + H))


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def compose(image_bytes: bytes, speaker: str, line: str, page: str = "") -> bytes:
    img = _fit_cover(Image.open(io.BytesIO(image_bytes)))
    draw = ImageDraw.Draw(img, "RGBA")
    pad, margin = 44, 48
    name_font, text_font = _font(40), _font(50)
    max_w = W - 2 * (margin + pad)

    size = 50
    lines = _wrap(draw, line, text_font, max_w)
    while len(lines) > 4 and size > 34:
        size -= 4
        text_font = _font(size)
        lines = _wrap(draw, line, text_font, max_w)

    line_h = int(size * 1.25)
    box_h = pad + 52 + len(lines) * line_h + pad - 10
    box = (margin, margin, W - margin, margin + box_h)
    draw.rounded_rectangle((box[0] + 6, box[1] + 10, box[2] + 6, box[3] + 10), 40, fill=(0, 0, 0, 40))
    draw.rounded_rectangle(box, 40, fill=(255, 255, 255, 240))

    color = CORAL if speaker == "Сам" else SLATE
    draw.text((box[0] + pad, box[1] + pad - 6), speaker, font=name_font, fill=color)
    y = box[1] + pad + 52
    for l in lines:
        draw.text((box[0] + pad, y), l, font=text_font, fill=INK)
        y += line_h

    if page:
        f = _font(30)
        tw = draw.textlength(page, font=f)
        draw.rounded_rectangle((W - margin - tw - 36, H - margin - 56, W - margin, H - margin), 28,
                               fill=(255, 255, 255, 200))
        draw.text((W - margin - tw - 18, H - margin - 48), page, font=f, fill=INK)

    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()
