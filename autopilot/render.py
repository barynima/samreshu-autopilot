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


# ---------- Reels и карусели ----------

RW, RH = 1080, 1920
CREAM = (255, 244, 236)
FONT_REGULAR = Path(__file__).resolve().parent.parent / "fonts" / "Font-Regular.ttf"


def _font_regular(size: int):
    if FONT_REGULAR.exists():
        return ImageFont.truetype(str(FONT_REGULAR), size)
    return _font(size)


def _fit_lines(draw, text, max_w, size, min_size, max_lines, bold=True):
    while True:
        font = _font(size) if bold else _font_regular(size)
        lines = _wrap(draw, text, font, max_w)
        if len(lines) <= max_lines or size <= min_size:
            return font, lines, size
        size -= 4


def _box(draw, xy, fill, radius=44, shadow=True):
    if shadow:
        draw.rounded_rectangle((xy[0] + 6, xy[1] + 10, xy[2] + 6, xy[3] + 10), radius, fill=(0, 0, 0, 45))
    draw.rounded_rectangle(xy, radius, fill=fill)


def hook_overlay(text: str) -> bytes:
    """Крупный крючок вверху кадра 9:16 — прозрачный PNG."""
    img = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font, lines, size = _fit_lines(draw, text, RW - 200, 74, 48, 4)
    lh = int(size * 1.22)
    h = len(lines) * lh + 90
    top = 230  # ниже шапки интерфейса Reels
    _box(draw, (60, top, RW - 60, top + h), (255, 255, 255, 245))
    y = top + 45
    for l in lines:
        w = draw.textlength(l, font=font)
        draw.text(((RW - w) / 2, y), l, font=font, fill=INK)
        y += lh
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def subtitle_overlay(speaker: str, line: str) -> bytes:
    """Субтитр с именем говорящего — в верхней части нижней трети, выше подписи и кнопок Reels."""
    img = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    if not line:
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()
    draw = ImageDraw.Draw(img)
    font, lines, size = _fit_lines(draw, line, RW - 260, 60, 42, 3)
    lh = int(size * 1.25)
    name_font = _font(40)
    h = 40 + 56 + len(lines) * lh + 30
    top = 1160
    _box(draw, (70, top, RW - 110, top + h), (255, 255, 255, 240), radius=40)
    color = CORAL if speaker == "Сам" else SLATE
    draw.text((110, top + 32), speaker, font=name_font, fill=color)
    y = top + 32 + 56
    for l in lines:
        draw.text((110, y), l, font=font, fill=INK)
        y += lh
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def fit_vertical(image_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    scale = max(RW / img.width, RH / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - RW) // 2, (img.height - RH) // 2
    return img.crop((left, top, left + RW, top + RH))


def reel_cover(frame_bytes: bytes, hook: str) -> bytes:
    base = fit_vertical(frame_bytes).convert("RGBA")
    over = Image.open(io.BytesIO(hook_overlay(hook)))
    base.alpha_composite(over)
    out = io.BytesIO()
    base.convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue()


def carousel_cover(image_bytes: bytes, title: str) -> bytes:
    img = _fit_cover(Image.open(io.BytesIO(image_bytes))).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")
    font, lines, size = _fit_lines(draw, title, W - 200, 84, 52, 3)
    lh = int(size * 1.2)
    h = len(lines) * lh + 90
    _box(draw, (60, 60, W - 60, 60 + h), (255, 255, 255, 245))
    y = 105
    for l in lines:
        w = draw.textlength(l, font=font)
        draw.text(((W - w) / 2, y), l, font=font, fill=INK)
        y += lh
    _swipe(draw)
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue()


def _swipe(draw):
    f = _font(34)
    t = "листайте →"
    tw = draw.textlength(t, font=f)
    draw.rounded_rectangle((W - 80 - tw - 40, H - 120, W - 60, H - 56), 32, fill=(255, 255, 255, 220))
    draw.text((W - 80 - tw - 20, H - 110), t, font=f, fill=INK)


def text_card(title: str, body: str, page: str, final: bool = False) -> bytes:
    bg = CORAL if final else CREAM
    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)
    ink = (255, 255, 255) if final else INK
    accent = (255, 255, 255) if final else CORAL
    y = 170
    if title:
        tfont, tlines, tsize = _fit_lines(draw, title, W - 220, 72, 50, 3)
        draw.rectangle((110, y - 40, 190, y - 30), fill=accent)
        for l in tlines:
            draw.text((110, y), l, font=tfont, fill=ink)
            y += int(tsize * 1.2)
        y += 50
    if final:
        bfont, blines, bsize = _fit_lines(draw, body, W - 220, 72, 48, 9, bold=True)
        y = max(y, (H - len(blines) * int(bsize * 1.4)) // 2)
    else:
        bfont, blines, bsize = _fit_lines(draw, body, W - 220, 54, 38, 11, bold=False)
    for l in blines:
        draw.text((110, y), l, font=bfont, fill=ink)
        y += int(bsize * 1.4)
    f = _font(30)
    draw.text((110, H - 110), "Сам решу", font=f, fill=accent)
    tw = draw.textlength(page, font=f)
    draw.text((W - 110 - tw, H - 110), page, font=f, fill=ink)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


# ---------- сторис для «Актуального» ----------

def _fit_paragraphs(draw, text, max_w, size, min_size, max_lines):
    """Как _fit_lines, но сохраняет переносы строк из текста (\n)."""
    while True:
        font = _font(size)
        lines = []
        for para in text.split("\n"):
            lines += _wrap(draw, para, font, max_w) or [""]
        if len(lines) <= max_lines or size <= min_size:
            return font, lines, size
        size -= 4


def _draw_centered(draw, lines, font, size, top, fill, left=None):
    lh = int(size * 1.28)
    y = top
    for l in lines:
        x = left if left is not None else (RW - draw.textlength(l, font=font)) / 2
        draw.text((x, y), l, font=font, fill=fill)
        y += lh
    return y


def _jpeg(img) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue()


def story(image_bytes: bytes, text: str) -> bytes:
    """Сторис 1080×1920: крупный текст в плашке под шапкой Instagram, персонажи ниже."""
    img = fit_vertical(image_bytes).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")
    font, lines, size = _fit_paragraphs(draw, text, RW - 240, 76, 44, 6)
    h = len(lines) * int(size * 1.28) + 100
    top = 260  # ниже полосок прогресса и аватарки
    _box(draw, (70, top, RW - 70, top + h), (255, 255, 255, 240))
    _draw_centered(draw, lines, font, size, top + 50, INK)
    return _jpeg(img)


def story_card(text: str, bg: str = "coral") -> bytes:
    """Сторис только с текстом: коралловый или кремовый фон, текст в верхней половине (ниже — место для стикера)."""
    coral = bg != "cream"
    img = Image.new("RGB", (RW, RH), CORAL if coral else CREAM)
    draw = ImageDraw.Draw(img)
    ink = (255, 255, 255) if coral else INK
    numbered = any(l.strip()[:2].rstrip(".").isdigit() for l in text.split("\n")[1:])
    font, lines, size = _fit_paragraphs(draw, text, RW - 220, 84 if not numbered else 64, 44, 12)
    draw.rectangle((110, 330, 200, 342), fill=(255, 255, 255) if coral else CORAL)
    _draw_centered(draw, lines, font, size, 400, ink, left=110)
    f = _font(34)
    draw.text((110, RH - 170), "Сам решу", font=f, fill=(255, 255, 255) if coral else CORAL)
    return _jpeg(img)
