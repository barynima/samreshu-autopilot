"""Сторис для «Актуального»: журнальная вёрстка 1080×1920 в фирменном стиле «Сам решу».

Типы слайдов (поле type в highlights.yaml):
  hero     — кадр из жизни на весь экран + крупный заголовок внизу (первый слайд хайлайта)
  number   — огромная цифра на коралловом фоне + пояснение
  myth     — «Миф» зачёркнут, ниже «На самом деле»
  list     — заголовок + пункты с номерами или галочками (label — дата/метка слева)
  qa       — вопрос мамы крупно + ответ
  chat     — переписка мессенджера (как в ленте) поверх кадра или «обоев»
  cta      — призыв на коралловом фоне, внизу место под стикер-ссылку
Слово в *звёздочках* выделяется маркером.
Безопасные зоны Instagram: сверху 250 px (полоски, аватар), снизу 280 px (поле ответа) — там ничего важного.
"""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import render

W, H = 1080, 1920
TOP, BOTTOM = 270, 1640
M = 90  # поля слева и справа
CORAL = (255, 122, 89)
CORAL_SOFT = (255, 205, 188)
CREAM = (255, 246, 239)
INK = (34, 34, 44)
GREY = (150, 150, 160)
WHITE = (255, 255, 255)
FONTS = Path(__file__).resolve().parent.parent / "fonts"


def _f(size, weight="Black"):
    return ImageFont.truetype(str(FONTS / f"Nunito-{weight}.ttf"), size)


# ---------- текст с выделениями ----------

def _tokens(text):
    """[(слово, выделено)]; короткие предлоги и тире приклеиваются к соседнему слову."""
    import re
    out = []
    for i, part in enumerate(re.split(r"\*", text)):
        for chunk in part.replace("\n", " \n ").split(" "):
            if chunk:
                out.append((chunk, i % 2 == 1))
    glued = []
    for w, e in out:
        prev = glued[-1] if glued else None
        if prev and prev[0] != "\n" and w != "\n" and w[0] in ",.!?:;»)…":
            glued[-1] = (prev[0] + w, prev[1])
            continue
        if prev and w != "\n" and prev[0] != "\n" and prev[1] == e and (
                w in ("—", "–") or (len(prev[0]) <= 2 and prev[0].isalpha())):
            glued[-1] = (prev[0] + "\u00a0" + w, e)
        else:
            glued.append((w, e))
    return glued


def _layout(text, font, max_w):
    lines, cur, cur_w = [], [], 0
    space = font.getlength(" ")
    for w, e in _tokens(text):
        if w == "\n":
            lines.append(cur)
            cur, cur_w = [], 0
            continue
        ww = font.getlength(w)
        if cur and cur_w + space + ww > max_w:
            lines.append(cur)
            cur, cur_w = [], 0
        cur.append((w, e, ww))
        cur_w += (space if len(cur) > 1 else 0) + ww
    if cur:
        lines.append(cur)
    return lines


def _fit(text, max_w, max_h, start, minimum, weight="Black", lh=1.12):
    size = start
    while size > minimum:
        font = _f(size, weight)
        lines = _layout(text, font, max_w)
        if len(lines) * size * lh <= max_h and all(sum(t[2] for t in l) + font.getlength(" ") * (len(l) - 1) <= max_w for l in lines):
            return font, lines, size
        size -= 4
    font = _f(minimum, weight)
    return font, _layout(text, font, max_w), minimum


def _draw_text(img, xy, text, max_w, max_h, start, minimum, color, mark, weight="Black", lh=1.12,
               align="left", valign="top", mark_text=None):
    """Рисует абзац; выделенные слова — маркером mark. Возвращает нижнюю границу."""
    font, lines, size = _fit(text, max_w, max_h, start, minimum, weight, lh)
    step = round(size * lh)
    x0, y = xy
    total = step * len(lines)
    if valign == "bottom":
        y -= total
    elif valign == "middle":
        y -= total // 2
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    td = ImageDraw.Draw(img)
    space = font.getlength(" ")
    for line in lines:
        lw = sum(t[2] for t in line) + space * (len(line) - 1)
        x = x0 + (max_w - lw) / 2 if align == "center" else x0
        # маркер под выделенными словами (сливаем соседние)
        i = 0
        while i < len(line):
            if line[i][1]:
                start_x = x + sum(t[2] for t in line[:i]) + space * i
                j = i
                while j + 1 < len(line) and line[j + 1][1]:
                    j += 1
                end_x = x + sum(t[2] for t in line[:j + 1]) + space * j
                d.rounded_rectangle((start_x - 12, y + size * 0.42, end_x + 12, y + size * 1.08), radius=12, fill=mark)
                i = j + 1
            else:
                i += 1
        y += step
    img.alpha_composite(layer)
    y = xy[1] - (total if valign == "bottom" else total // 2 if valign == "middle" else 0)
    for line in lines:
        lw = sum(t[2] for t in line) + space * (len(line) - 1)
        x = x0 + (max_w - lw) / 2 if align == "center" else x0
        for w, e, ww in line:
            td.text((x, y), w, font=font, fill=(mark_text or color) if e else color)
            x += ww + space
        y += step
    return y


# ---------- общие элементы ----------

def _chrome(img, kicker, page, dark=False):
    """Метка рубрики слева сверху и номер слайда справа."""
    d = ImageDraw.Draw(img)
    f = _f(34, "ExtraBold")
    col = WHITE if dark else CORAL
    if kicker:
        text = kicker.upper()
        tw = f.getlength(text) + sum(4 for _ in text)
        d.rounded_rectangle((M, TOP, M + tw + 56, TOP + 66), radius=33, outline=col, width=4)
        x = M + 28
        for ch in text:
            d.text((x, TOP + 33), ch, font=f, fill=col, anchor="lm")
            x += f.getlength(ch) + 4
    if page:
        d.text((W - M, TOP + 33), page, font=f, fill=col, anchor="rm")


def _brand(img, dark=False):
    d = ImageDraw.Draw(img)
    d.text((M, BOTTOM + 60), "Сам решу", font=_f(38, "ExtraBold"), fill=WHITE if dark else CORAL, anchor="lm")
    d.text((W - M, BOTTOM + 60), "samreshu.pro", font=_f(34, "Bold"), fill=WHITE if dark else GREY, anchor="rm")


def _photo(image_bytes):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    sc = max(W / img.width, H / img.height)
    img = img.resize((round(img.width * sc), round(img.height * sc)), Image.LANCZOS)
    l, t = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((l, t, l + W, t + H)).convert("RGBA")


def _gradient(img, start_frac=0.38, strength=235):
    g = Image.new("L", (1, H), 0)
    for y in range(H):
        k = max(0.0, (y / H - start_frac) / (1 - start_frac))
        g.putpixel((0, y), int(strength * min(1.0, k ** 0.9)))
    shade = Image.new("RGBA", (W, H), (20, 16, 24, 255))
    shade.putalpha(g.resize((W, H)))
    img.alpha_composite(shade)
    top = Image.new("L", (1, H), 0)
    for y in range(0, 420):
        top.putpixel((0, y), int(110 * (1 - y / 420)))
    sh2 = Image.new("RGBA", (W, H), (20, 16, 24, 255))
    sh2.putalpha(top.resize((W, H)))
    img.alpha_composite(sh2)


def _jpeg(img):
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=93)
    return out.getvalue()


def _placeholder():
    img = Image.new("RGB", (W, H), (200, 190, 182))
    d = ImageDraw.Draw(img)
    for y in range(H):
        c = int(215 - 60 * y / H)
        d.line((0, y, W, y), fill=(c + 25, c + 10, c))
    return img


# ---------- типы слайдов ----------

def hero(s, kicker, page, image_bytes=None):
    img = _photo(image_bytes) if image_bytes else _placeholder().convert("RGBA")
    _gradient(img)
    _chrome(img, kicker, page, dark=True)
    y = BOTTOM - 20
    if s.get("sub"):
        _draw_text(img, (M, y), s["sub"], W - 2 * M, 260, 50, 38, (255, 255, 255, 235), CORAL + (255,),
                   weight="Bold", lh=1.25, valign="bottom")
        y -= _height(s["sub"], 260, 50, 38, "Bold", 1.25) + 40
    _draw_text(img, (M, y), s["title"], W - 2 * M, 620, 112, 70, WHITE, CORAL + (255,), valign="bottom")
    _brand(img, dark=True)
    return _jpeg(img)


def _height(text, max_h, start, minimum, weight, lh):
    font, lines, size = _fit(text, W - 2 * M, max_h, start, minimum, weight, lh)
    return round(size * lh) * len(lines)


def number(s, kicker, page, _img=None):
    img = Image.new("RGBA", (W, H), CORAL + (255,))
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    for r, a in ((760, 26), (520, 22)):
        dd.ellipse((W + 60 - r // 2, 1500 - r // 2, W + 60 + r // 2, 1500 + r // 2), fill=(255, 255, 255, a))
    img.alpha_composite(deco)
    _chrome(img, kicker, page, dark=True)
    L = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(L)
    big = s["number"]
    size = 400
    while _f(size).getlength(big) > W - 2 * M and size > 140:
        size -= 10
    d.text((M - 8, 500), big, font=_f(size), fill=WHITE, anchor="ls")
    top = 500 - round(size * 0.74)
    y = _draw_text(L, (M, 560), s["text"], W - 2 * M, 520, 80, 52, WHITE, (255, 255, 255, 70))
    if s.get("note"):
        y = _draw_text(L, (M, y + 50), s["note"], W - 2 * M, 300, 46, 34, (255, 255, 255, 225), (255, 255, 255, 60),
                       weight="Bold", lh=1.3)
    _place(img, L, top, y)
    _brand(img, dark=True)
    return _jpeg(img)


def _place(img, layer, top, bottom, area=(400, BOTTOM - 40), bias=0.42):
    """Переносит блок контента [top, bottom] из layer в оптическую середину свободной зоны."""
    h = bottom - top
    new_top = area[0] + max(0, (area[1] - area[0] - h)) * bias
    block = layer.crop((0, int(top), W, int(min(H, bottom + 40))))
    img.alpha_composite(block, dest=(0, int(new_top)))


def _pill(d, x, y, text, fill, color):
    f = _f(36, "ExtraBold")
    tw = f.getlength(text)
    d.rounded_rectangle((x, y, x + tw + 60, y + 70), radius=35, fill=fill)
    d.text((x + 30, y + 35), text, font=f, fill=color, anchor="lm")


def myth(s, kicker, page, _img=None):
    img = Image.new("RGBA", (W, H), CREAM + (255,))
    _chrome(img, kicker, page)
    L = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(L)
    _pill(d, M, 470, "МИФ", (232, 228, 226), GREY)
    font, lines, size = _fit(s["myth"], W - 2 * M, 360, 80, 50, "ExtraBold")
    y = 580
    for line in lines:
        text = " ".join(t[0] for t in line)
        d.text((M, y), text, font=font, fill=GREY)
        lw = font.getlength(text)
        d.line((M - 6, y + size * 0.62, M + lw + 6, y + size * 0.62), fill=CORAL, width=7)
        y += round(size * 1.18)
    y += 70
    d.line((M, y, M + 140, y), fill=CORAL, width=8)
    y += 70
    _pill(d, M, y, "НА САМОМ ДЕЛЕ", CORAL, WHITE)
    y = _draw_text(L, (M, y + 120), s["truth"], W - 2 * M, BOTTOM - y - 160, 108, 60, INK, CORAL_SOFT + (255,))
    _place(img, L, 470, y)
    _brand(img)
    return _jpeg(img)


def _check(d, x, y, r, mark, n=None):
    d.ellipse((x, y, x + 2 * r, y + 2 * r), fill=CORAL)
    if n is not None:
        d.text((x + r, y + r), str(n), font=_f(round(r * 1.1)), fill=WHITE, anchor="mm")
    else:
        d.line((x + r * 0.5, y + r * 1.02, x + r * 0.88, y + r * 1.38), fill=WHITE, width=9)
        d.line((x + r * 0.86, y + r * 1.4, x + r * 1.5, y + r * 0.6), fill=WHITE, width=9)


def list_(s, kicker, page, _img=None):
    img = Image.new("RGBA", (W, H), CREAM + (255,))
    _chrome(img, kicker, page)
    L = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(L)
    y = _draw_text(L, (M, 440), s["title"], W - 2 * M, 380, 104, 64, INK, CORAL_SOFT + (255,)) + 50
    items = s["items"]
    room = (BOTTOM - 40 - y) / len(items)
    numbered = s.get("style") == "numbers"
    for i, it in enumerate(items, 1):
        label = it.get("label") if isinstance(it, dict) else None
        text = it["text"] if isinstance(it, dict) else it
        if label:
            d.text((M, y + 4), label, font=_f(44, "Black"), fill=CORAL)
            ty = y + 66
            bottom = _draw_text(L, (M, ty), text, W - 2 * M, room - 80, 64, 42, INK, CORAL_SOFT + (255,),
                                weight="ExtraBold", lh=1.2)
            d.line((M, bottom + 26, W - M, bottom + 26), fill=(240, 220, 210), width=3)
            y = bottom + 60
        else:
            _check(d, M, y + 4, 38, None, i if numbered else None)
            bottom = _draw_text(L, (M + 110, y), text, W - 2 * M - 110, room - 30, 68, 42, INK,
                                CORAL_SOFT + (255,), weight="ExtraBold", lh=1.2)
            y = max(bottom, y + 90) + 44
    _place(img, L, 440, y - 44)
    _brand(img)
    return _jpeg(img)


def qa(s, kicker, page, _img=None):
    img = Image.new("RGBA", (W, H), CREAM + (255,))
    d = ImageDraw.Draw(img)
    d.text((W - 40, 300), "?", font=_f(900), fill=(255, 226, 214), anchor="ra")
    _chrome(img, kicker, page)
    L = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    y = _draw_text(L, (M, 470), s["q"], W - 2 * M - 60, 560, 104, 64, INK, CORAL_SOFT + (255,))
    ImageDraw.Draw(L).line((M, y + 50, M + 140, y + 50), fill=CORAL, width=8)
    y = _draw_text(L, (M, y + 120), s["a"], W - 2 * M, BOTTOM - y - 160, 74, 46, INK, CORAL_SOFT + (255,),
               weight="ExtraBold", lh=1.22)
    _place(img, L, 470, y)
    _brand(img)
    return _jpeg(img)


def chat(s, kicker, page, image_bytes=None):
    return render.story_chat(s["messages"], image_bytes)


def cta(s, kicker, page, _img=None, auto=False):
    img = Image.new("RGBA", (W, H), CORAL + (255,))
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(deco).ellipse((-300, 1260, 520, 2080), fill=(255, 255, 255, 26))
    img.alpha_composite(deco)
    d = ImageDraw.Draw(img)
    _chrome(img, kicker, page, dark=True)
    y = _draw_text(img, (M, 470), s["title"], W - 2 * M, 560, 112, 70, WHITE, (255, 255, 255, 70))
    if s.get("sub"):
        y = _draw_text(img, (M, y + 40), s["sub"], W - 2 * M, 300, 52, 38, (255, 255, 255, 235), (255, 255, 255, 60),
                       weight="Bold", lh=1.3)
    # стрелка вниз к стикеру
    if auto:  # публикует бот: стикер-ссылку не добавить, поэтому ведём в шапку профиля
        d = ImageDraw.Draw(img)
        by = max(y + 110, 1150)
        f1, f2 = _f(64, "Black"), _f(40, "ExtraBold")
        d.rounded_rectangle((M, by, W - M, by + 230), radius=60, fill=WHITE)
        d.text((W // 2, by + 82), "samreshu.pro", font=f1, fill=CORAL, anchor="mm")
        d.text((W // 2, by + 160), "ссылка — в шапке профиля", font=f2, fill=INK, anchor="mm")
        _brand(img, dark=True)
        return _jpeg(img)
    arrows = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(arrows)
    cx, ay = W // 2, max(y + 90, 1180)
    for k in range(3):
        yy = ay + k * 46
        a = 255 - k * 70
        d.line((cx - 46, yy, cx, yy + 40), fill=(255, 255, 255, a), width=12)
        d.line((cx, yy + 40, cx + 46, yy), fill=(255, 255, 255, a), width=12)
    img.alpha_composite(arrows)
    _brand(img, dark=True)
    return _jpeg(img)


KINDS = {"hero": hero, "number": number, "myth": myth, "list": list_, "qa": qa, "chat": chat, "cta": cta}


def slide(s, kicker, page, image_bytes=None, auto=False):
    """auto=True — версия для публикации ботом (без мест под ручные стикеры)."""
    kind = s.get("type", "chat")
    if kind == "cta":
        return cta(s, kicker, page, image_bytes, auto=auto)
    return KINDS[kind](s, kicker, page, image_bytes)
