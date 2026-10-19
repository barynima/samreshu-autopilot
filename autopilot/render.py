"""Накладывает реплику на кадр: Instagram-формат 1080×1350, JPEG."""
import io
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1350
CORAL = (255, 122, 89)
SLATE = (110, 120, 140)
INK = (40, 40, 48)

FONT_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "fonts" / "Nunito-ExtraBold.ttf",
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
    words = []
    for w in text.split():  # тире и короткие предлоги не отрываем
        if words and (w in ("—", "–") or len(words[-1]) <= 2 and words[-1].isalpha()):
            words[-1] += " " + w
        else:
            words.append(w)
    for word in words:
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
FONT_REGULAR = Path(__file__).resolve().parent.parent / "fonts" / "Nunito-SemiBold.ttf"


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
    """Субтитр вверху кадра (там, где по композиции пустая стена), чтобы не закрывать персонажей."""
    img = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    if not line:
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()
    draw = ImageDraw.Draw(img)
    text = f"{speaker}: {line}" if speaker else line
    font, lines, size = _fit_lines(draw, text, RW - 220, 58, 42, 3)
    lh = int(size * 1.25)
    h = len(lines) * lh + 70
    top = 230  # ниже шапки интерфейса Reels
    _box(draw, (60, top, RW - 60, top + h), (255, 255, 255, 240), radius=40)
    color = CORAL if speaker == "Сам" else SLATE
    y = top + 35
    for n, l in enumerate(lines):
        x = 100
        if n == 0 and speaker and l.startswith(speaker + ":"):
            name = speaker + ":"
            draw.text((x, y), name, font=font, fill=color)
            x += draw.textlength(name + " ", font=font)
            l = l[len(name):].lstrip()
        draw.text((x, y), l, font=font, fill=INK)
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


# ---------- текстовые Reels ----------

def empty_overlay() -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (RW, RH), (0, 0, 0, 0)).save(out, "PNG")
    return out.getvalue()


def slide_overlay(text: str, big: bool = False) -> bytes:
    """Крупная надпись вверху кадра (над персонажами). Слова в *звёздочках* — коралловым."""
    img = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    hot = set()
    for m in re.findall(r"\*([^*]+)\*", text):
        hot.update(w.strip(".,!?:;«»—") .lower() for w in m.split())
    plain = text.replace("*", "")
    font, lines, size = _fit_paragraphs(draw, plain, RW - 200, 92 if big else 82, 50, 5)
    lh = int(size * 1.22)
    h = len(lines) * lh + 90
    top = 230  # ниже шапки Reels, на пустой стене над персонажами
    _box(draw, (50, top, RW - 50, top + h), (255, 255, 255, 245), radius=48)
    y = top + 45
    space = draw.textlength(" ", font=font)
    for line in lines:
        words = line.split()
        x = (RW - draw.textlength(line, font=font)) / 2
        for w in words:
            color = CORAL if w.strip(".,!?:;«»—").lower() in hot else INK
            draw.text((x, y), w, font=font, fill=color)
            x += draw.textlength(w, font=font) + space
        y += lh
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def end_card(text: str) -> bytes:
    """Финальная карточка ролика: вывод и призыв на коралловом фоне."""
    img = Image.new("RGB", (RW, RH), CORAL)
    draw = ImageDraw.Draw(img)
    font, lines, size = _fit_paragraphs(draw, text, RW - 220, 80, 48, 7)
    h = len(lines) * int(size * 1.28)
    _draw_centered(draw, lines, font, size, max(360, 820 - h // 2), (255, 255, 255))
    f = _font(40)
    t = "Сам решу"
    draw.text(((RW - draw.textlength(t, font=f)) / 2, 1420), t, font=f, fill=(255, 255, 255))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


# ---------- стиль «переписка»: пузыри мессенджера ----------
# mom — вопрос мамы (белый, справа), sam — ответ ребёнка от имени Сама (коралловый, слева, с аватаркой),
# brand — сообщение «Сам решу» (белый, слева, с аватаркой). Слова в *звёздочках* выделяются.

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
AVATAR = Path(__file__).resolve().parent.parent / "profile" / "avatar.jpg"
LIGHT = (255, 233, 190)


def _nunito(weight: str, size: int):
    p = FONTS_DIR / f"Nunito-{weight}.ttf"
    return ImageFont.truetype(str(p), size) if p.exists() else _font(size)


def _tokens(text: str):
    out, hot = [], False
    for part in re.split(r"(\*)", text):
        if part == "*":
            hot = not hot
            continue
        for w in part.split():
            if out and all(c in ".,!?:;…»" for c in w):
                out[-1] = (out[-1][0] + w, out[-1][1])
            else:
                out.append((w, hot))
    glued = []
    for w, h in out:  # короткие предлоги и союзы не оставляем в конце строки
        if glued and len(glued[-1][0]) <= 2 and glued[-1][0].isalpha():
            pw, ph = glued.pop()
            glued.append((pw + "\u00a0" + w, h or ph))
        else:
            glued.append((w, h))
    return glued


def _wrap_tokens(draw, toks, fonts, max_w):
    lines = [[]]
    sp = draw.textlength(" ", font=fonts[False])
    for w, h in toks:
        cur = lines[-1]
        width = sum(draw.textlength(t, font=fonts[hh]) for t, hh in cur) + sp * len(cur) + draw.textlength(w, font=fonts[h])
        if cur and width > max_w:
            lines.append([])
        lines[-1].append((w, h))
    return lines


def _avatar(size: int):
    a = Image.open(AVATAR).convert("RGBA").resize((size, size), Image.LANCZOS)
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size - 1, size - 1), fill=255)
    a.putalpha(m)
    return a


def _soft_shadow(base, box, r, off=(0, 8), blur=14, alpha=55):
    from PIL import ImageFilter
    sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((box[0] + off[0], box[1] + off[1], box[2] + off[0], box[3] + off[1]), r,
                                         fill=(0, 0, 0, alpha))
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))


def _ticks(draw, x, y, col):
    for dx in (0, 14):
        draw.line((x + dx, y, x + 8 + dx, y + 8, x + 22 + dx, y - 10), fill=col, width=4)


STYLE = {
    "mom": dict(side="right", fill=(255, 255, 255), ink=INK, hot=CORAL, name=None, avatar=False),
    "sam": dict(side="left", fill=CORAL, ink=(255, 255, 255), hot=LIGHT, name="Сам", avatar=True),
    "brand": dict(side="left", fill=(255, 255, 255), ink=INK, hot=CORAL, name="Сам решу", avatar=True),
}


def _bubble(base, draw, who, text, y, time="21:47", size=70):
    st = STYLE[who]
    fonts = {False: _nunito("ExtraBold", size), True: _nunito("Black", size)}
    fn, ft = _nunito("ExtraBold", 32), _nunito("SemiBold", 28)
    lines = _wrap_tokens(draw, _tokens(text), fonts, RW - 400)
    lh = int(size * 1.23)
    sp = draw.textlength(" ", font=fonts[False])
    w = max(sum(draw.textlength(t, font=fonts[h]) for t, h in ln) + sp * (len(ln) - 1) for ln in lines)
    if st["name"]:
        w = max(w, draw.textlength(st["name"], font=fn))
    bw, bh = max(int(w + 90), 330), len(lines) * lh + (110 if st["name"] else 70) + 30
    x0 = 160 if st["side"] == "left" else RW - 60 - bw
    x1, y0, y1 = x0 + bw, y, y + bh
    _soft_shadow(base, (x0, y0, x1, y1), 40)
    draw.rounded_rectangle((x0, y0, x1, y1), 40, fill=st["fill"] + (252,))
    if st["side"] == "left":
        draw.polygon([(x0 + 4, y1 - 56), (x0 + 4, y1 - 8), (x0 - 24, y1 - 4)], fill=st["fill"] + (252,))
    else:
        draw.polygon([(x1 - 4, y1 - 56), (x1 - 4, y1 - 8), (x1 + 24, y1 - 4)], fill=st["fill"] + (252,))
    yy = y0 + 30
    if st["name"]:
        draw.text((x0 + 44, yy), st["name"], font=fn, fill=st["hot"] if who == "brand" else LIGHT)
        yy += 46
    for ln in lines:
        x = x0 + 44
        for t, h in ln:
            draw.text((x, yy), t, font=fonts[h], fill=st["hot"] if h else st["ink"])
            x += draw.textlength(t, font=fonts[h]) + sp
        yy += lh
    coral = st["fill"] == CORAL
    draw.text((x1 - 70, y1 - 20), time, font=ft, fill=(255, 255, 255, 210) if coral else (150, 150, 160), anchor="rb")
    _ticks(draw, x1 - 58, y1 - 34, (255, 255, 255) if coral else (90, 160, 230))
    if st["avatar"]:
        base.alpha_composite(_avatar(100), (40, y1 - 100))
    return y1


def _typing(base, draw, who, y):
    st = STYLE[who]
    x0, x1, y1 = 160, 160 + 170, y + 96
    _soft_shadow(base, (x0, y, x1, y1), 40)
    draw.rounded_rectangle((x0, y, x1, y1), 40, fill=st["fill"] + (252,))
    draw.polygon([(x0 + 4, y1 - 56), (x0 + 4, y1 - 8), (x0 - 24, y1 - 4)], fill=st["fill"] + (252,))
    dot = (255, 255, 255) if st["fill"] == CORAL else (170, 170, 178)
    for i in range(3):
        cx = x0 + 50 + i * 36
        draw.ellipse((cx - 11, y + 37, cx + 11, y + 59), fill=dot)
    base.alpha_composite(_avatar(100), (40, y1 - 100))
    f = _nunito("Bold", 30)
    draw.text((x1 + 24, y1 - 40), f"{st['name']} печатает…", font=f, fill=(255, 255, 255),
              stroke_width=3, stroke_fill=(0, 0, 0, 120))
    return y1


def chat_overlay(messages: list, typing: str = None, top: int = 230) -> bytes:
    """Прозрачный PNG 1080×1920: пузыри переписки сверху вниз. messages: [{"who", "text"}]."""
    img = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    y = top
    for i, m in enumerate(messages):
        y = _bubble(img, draw, m["who"], m["text"], y, time="21:46" if m["who"] == "mom" else "21:47") + 28
    if typing:
        _typing(img, draw, typing, y)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def chat_cover(image_bytes: bytes, messages: list, size=(RW, RH)) -> bytes:
    """Обложка (Reels 9:16 или карусель 4:5): кадр + переписка."""
    w, h = size
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    sc = max(w / img.width, h / img.height)
    img = img.resize((round(img.width * sc), round(img.height * sc)), Image.LANCZOS)
    l, t = (img.width - w) // 2, (img.height - h) // 2
    base = img.crop((l, t, l + w, t + h)).convert("RGBA")
    over = Image.open(io.BytesIO(chat_overlay(messages, top=230 if h == RH else 70)))
    if (w, h) != (RW, RH):
        over = over.crop((0, 0, w, h))
    base.alpha_composite(over)
    out = io.BytesIO()
    base.convert("RGB").save(out, "JPEG", quality=92)
    return out.getvalue()
