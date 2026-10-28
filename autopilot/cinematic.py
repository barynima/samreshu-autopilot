"""Reels «Кино»: короткая драма по кадрам, как в сценарии SYNTX «Попытка №47».

Сценарий (script) — JSON:
  cast:   [{"id": "mom", "prompt": "EN портрет/локация"}...]  — опорные изображения: герои и места не «переезжают»
  shots:  [{"sec": 2.0, "refs": ["mom", "door"], "visual": "EN кадр", "motion": "EN движение",
            "texts": [{"at": 0, "to": 2, "pos": "top|center|counter", "lines": [{"t": "…", "w": "regular|bold"}]}
                      или {"at": .., "to": .., "pos": "chat", "messages": [{"who": "mom", "text": "…"}]}],
            "vo": {"at": 0.2, "text": "закадр", "voice": "mom|teen"},
            "sfx": [{"at": 1.8, "type": "knock|click|tick|bolt|send|thud"}]}]
  cover_shot: номер кадра для обложки, cover_lines: [{"t", "w"}]
  music:  EN описание музыки (если в библиотеке нет своих треков)
Слово в *звёздочках* в надписи — акцентным цветом.
"""
import io
import math
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import images, render, text, video

W, H = 1080, 1920
FPS = 30
ACCENT = (255, 138, 61)  # тёплый оранжевый, как в сценарии SYNTX
FONTS = Path(__file__).resolve().parent.parent / "fonts"

CINE_STYLE = ("Photorealistic still from a modern Russian-language family drama series, shot on a cinema camera, 35mm. "
              "Modern contemporary 2020s apartment in a new residential building: light walls, light oak laminate, "
              "recessed ceiling spotlights, warm LED strips, light modern interior doors, Scandinavian furniture; "
              "lived-in, tidy. No Soviet-style interior, no old wallpaper, no carpets on walls, no worn furniture. "
              "Warm white and soft cold blue light contrast, cinematic color grading, subtle film grain, but NOT gloomy — "
              "warm, intimate, hopeful. ANATOMY PERFECT: every person has exactly two arms, two hands, five fingers; "
              "people sit firmly and stand on the floor; one continuous room with consistent walls and ceiling. "
              "Vertical 9:16, no text, no letters, no captions, no watermark, no logos.")
CINE_MOTION = "Realistic motion, no morphing, stable faces, no text appearing, static or very slow camera."


def _f(size, w="ExtraBold"):
    name = {"regular": "SemiBold", "bold": "ExtraBold", "black": "Black"}.get(w, w)
    return ImageFont.truetype(str(FONTS / f"Nunito-{name}.ttf"), size)


# ---------- надписи ----------

def _split_accent(t):
    import re
    out = []
    for i, part in enumerate(re.split(r"\*", t)):
        for w in part.split(" "):
            if w:
                out.append((w, i % 2 == 1))
    return out


def _wrap(tokens, font, max_w):
    lines, cur, cw = [], [], 0
    sp = font.getlength(" ")
    for w, a in tokens:
        ww = font.getlength(w)
        if cur and cw + sp + ww > max_w:
            lines.append(cur)
            cur, cw = [], 0
        cur.append((w, a, ww))
        cw += (sp if len(cur) > 1 else 0) + ww
    if cur:
        lines.append(cur)
    return lines


def scrim(pos="top") -> Image.Image:
    """Мягкое затемнение под надпись, чтобы белый текст читался на любом кадре."""
    g = Image.new("L", (1, H), 0)
    for y in range(H):
        if pos == "top":
            k = 1 - min(1, max(0, (y - 120) / 900))
        else:
            k = max(0, 1 - abs(y - H * 0.42) / 520)
        g.putpixel((0, y), int(150 * k ** 1.2))
    layer = Image.new("RGBA", (W, H), (10, 8, 14, 255))
    layer.putalpha(g.resize((W, H)))
    return layer


def text_layer(block) -> Image.Image:
    """Прозрачный слой 1080×1920 с одной надписью (стили из сценария SYNTX)."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pos = block.get("pos", "top")
    if pos == "chat":
        return Image.open(io.BytesIO(render.chat_overlay(block["messages"], top=640))).convert("RGBA")
    if pos == "counter":
        d = ImageDraw.Draw(img)
        t = " ".join(l["t"] for l in block["lines"]).replace("*", "")
        f = _f(44, "black")
        tw = f.getlength(t)
        x1, y0 = W - 160, 220
        x0 = x1 - tw - 48
        d.rounded_rectangle((x0, y0, x1, y0 + 76), radius=14, fill=(255, 255, 255, 205))
        d.text((x0 + 24, y0 + 38), t, font=f, fill=(30, 30, 30), anchor="lm")
        return img
    max_w = W - 2 * 150
    rendered = []  # (font, lines, size)
    for ln in block["lines"]:
        bold = ln.get("w", "bold") != "regular"
        size = ln.get("size") or (78 if bold else 56)
        if pos == "center":
            size = ln.get("size") or 66
        while True:
            f = _f(size, "bold" if bold else "regular")
            lines = _wrap(_split_accent(ln["t"]), f, max_w)
            if len(lines) <= 3 or size <= 44:
                break
            size -= 4
        rendered.append((f, lines, size))
    total = sum(round(s * 1.18) * len(ls) for _, ls, s in rendered) + 10 * (len(rendered) - 1)
    y = {"top": 300, "center": (H - total) // 2 - 80}.get(pos, 300)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd, d = ImageDraw.Draw(shadow), ImageDraw.Draw(img)
    for f, lines, size in rendered:
        sp = f.getlength(" ")
        for line in lines:
            lw = sum(t[2] for t in line) + sp * (len(line) - 1)
            x = (W - lw) / 2
            for w, a, ww in line:
                sd.text((x + 2, y + 4), w, font=f, fill=(0, 0, 0, 150))
                d.text((x, y), w, font=f, fill=ACCENT + (255,) if a else (255, 255, 255, 255))
                x += ww + sp
            y += round(size * 1.18)
        y += 10
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))
    shadow.alpha_composite(img)
    out = scrim(pos)
    out.alpha_composite(shadow)
    return out


# ---------- звуковые эффекты (синтез, без внешних файлов) ----------

def _sfx(kind: str, sr=48000) -> bytes:
    import random
    random.seed(kind)
    if kind == "tick":
        n, f = int(sr * 0.04), lambda t: math.sin(2 * math.pi * 2400 * t) * math.exp(-t * 120)
    elif kind == "knock":
        n, f = int(sr * 0.25), lambda t: (math.sin(2 * math.pi * 140 * t) + 0.4 * random.uniform(-1, 1)) * math.exp(-t * 28)
    elif kind == "bolt":
        n, f = int(sr * 0.18), lambda t: (0.6 * random.uniform(-1, 1) + math.sin(2 * math.pi * 900 * t)) * math.exp(-t * 45)
    elif kind == "send":
        n, f = int(sr * 0.18), lambda t: math.sin(2 * math.pi * (900 + 2600 * t) * t) * math.exp(-t * 18)
    elif kind == "thud":
        n, f = int(sr * 0.3), lambda t: math.sin(2 * math.pi * 90 * t) * math.exp(-t * 20)
    else:  # click
        n, f = int(sr * 0.05), lambda t: random.uniform(-1, 1) * math.exp(-t * 160)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wv:
        wv.setnchannels(1)
        wv.setsampwidth(2)
        wv.setframerate(sr)
        wv.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, f(i / sr) * 0.6)) * 32767)) for i in range(n)))
    return buf.getvalue()


# ---------- голос ----------

NARRATOR = ["ru-RU-Chirp3-HD-Aoede", "ru-RU-Wavenet-C", "ru-RU-Standard-C"]
TEEN = ["ru-RU-Chirp3-HD-Puck", "ru-RU-Wavenet-B", "ru-RU-Standard-B"]


def _tts(line: str, voices: list, rate=0.95) -> bytes:
    import base64
    import requests
    from . import google
    last = None
    for name in voices:
        h = {"Authorization": f"Bearer {google._credentials().token}",
             "x-goog-user-project": google._credentials().project_id}
        cfg = {"audioEncoding": "LINEAR16", "sampleRateHertz": 48000}
        if "Chirp3" not in name:
            cfg["speakingRate"] = rate
        r = requests.post("https://texttospeech.googleapis.com/v1/text:synthesize", headers=h, timeout=60, json={
            "input": {"text": line}, "voice": {"languageCode": "ru-RU", "name": name}, "audioConfig": cfg})
        if r.status_code == 200:
            return base64.b64decode(r.json()["audioContent"])
        last = f"{name}: HTTP {r.status_code} {r.text[:160]}"
    raise RuntimeError(f"Голос не синтезировался ({last})")


# ---------- кадры ----------

def _shot_image(shot, refs: dict, allow_face=True):
    """Кадр по опорным изображениям; до 3 попыток с проверкой на брак. Возвращает (bytes|None, отчёт)."""
    import base64
    best = (None, None)
    for _ in range(3):
        parts = []
        names = []
        for rid in shot.get("refs") or []:
            if refs.get(rid):
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(refs[rid]).decode()}})
                names.append(rid)
        keep = (f"The attached images are references ({', '.join(names)}): keep exactly the same people (faces, hair, "
                "clothes) and the same rooms, doors and furniture. " if names else "")
        parts.append({"text": f"{keep}Shot: {shot['visual']}\n{CINE_STYLE}"})
        try:
            img = images._call(parts, "9:16")
        except Exception as e:  # noqa: BLE001
            best = best if best[0] else (None, {"problems": [str(e)[:200]], "score": 0})
            continue
        rep = text.check_photo(img, shot["visual"], allow_teen_face=allow_face)
        if rep["ok"]:
            return img, rep
        if not best[1] or rep["score"] > best[1].get("score", 0):
            bad = any(w in p.lower() for p in rep["problems"] for w in ("рук", "пальц", "букв", "надпис", "текст"))
            best = (img if rep["score"] >= 6 and not bad else None, rep)
    return best


def _jpg(data: bytes) -> bytes:
    pic = render.fit_vertical(data)
    out = io.BytesIO()
    pic.save(out, "JPEG", quality=93)
    return out.getvalue()


def render_reel(script: dict, out_path: Path, with_music=True, veo=False) -> dict:
    """Собирает ролик. Возвращает {"warnings", "cover", "pictures"}."""
    warnings, pictures = [], []
    refs = {}
    for c in script.get("cast") or []:
        try:
            refs[c["id"]] = _jpg(images._call([{"text": f"{c['prompt']}\n{CINE_STYLE}"}], "9:16"))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"Опорное изображение «{c['id']}» не получилось: {str(e)[:120]}")
    frames = []
    for i, shot in enumerate(script["shots"], 1):
        img, rep = _shot_image(shot, refs)
        if img is None:
            img = next((refs[r] for r in reversed(shot.get("refs") or []) if refs.get(r)), None) \
                or (frames[-1] if frames else None) or next(iter(refs.values()), None) or render._chat_wallpaper()
            warnings.append(f"Кадр {i} не прошёл проверку ({'; '.join((rep or {}).get('problems', [])[:2])}) — "
                            "поставила запасной кадр.")
        frames.append(_jpg(img))
    # обложка
    ci = max(0, min(len(frames) - 1, int(script.get("cover_shot", 1)) - 1))
    cover_img = Image.open(io.BytesIO(frames[ci])).convert("RGBA")
    if script.get("cover_lines"):
        cover_img.alpha_composite(text_layer({"pos": "top", "lines": script["cover_lines"]}))
    buf = io.BytesIO()
    cover_img.convert("RGB").save(buf, "JPEG", quality=92)
    cover = buf.getvalue()

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts, t0, audio = [], 0.0, []  # audio: (start_sec, wav_path, volume, lowpass)
        for i, (shot, frame) in enumerate(zip(script["shots"], frames)):
            dur = float(shot.get("sec") or 3)
            pic = tmp / f"f{i}.png"
            Image.open(io.BytesIO(frame)).save(pic)
            clip = None
            if veo:
                vsec = 4 if dur <= 4 else 6 if dur <= 6 else 8  # Veo умеет только 4, 6 и 8 секунд
                try:
                    data = video.veo_clip(frame, f"{shot.get('motion', '')} {CINE_MOTION}", vsec,
                                          style=video.LIFE_VIDEO, models=video.VEO_FAST)
                    if not video.clip_problem(data, frame, vsec):
                        clip = tmp / f"c{i}.mp4"
                        clip.write_bytes(data)
                except Exception as e:  # noqa: BLE001
                    warnings.append(f"Veo, кадр {i + 1}: {str(e)[:100]}")
            layers = []
            for j, b in enumerate(shot.get("texts") or []):
                png = tmp / f"t{i}_{j}.png"
                text_layer(b).save(png)
                layers.append((png, float(b.get("at", 0)), float(b.get("to", dur))))
            out = tmp / f"p{i}.mp4"
            video._still_multi(pic, layers, dur, out, zoom=True, clip=clip)
            parts.append(out)
            vo = shot.get("vo")
            if vo and vo.get("text"):
                try:
                    wav = tmp / f"vo{i}.wav"
                    teen = vo.get("voice") == "teen"
                    wav.write_bytes(_tts(vo["text"], TEEN if teen else NARRATOR, rate=1.0 if teen else 0.92))
                    audio.append((t0 + float(vo.get("at", 0.2)), wav, 1.6 if not teen else 1.2, teen))
                except Exception as e:  # noqa: BLE001
                    if not any("закадрового" in w for w in warnings):
                        warnings.append(f"Без закадрового голоса: {str(e)[:150]}")
            for k, s in enumerate(shot.get("sfx") or []):
                wav = tmp / f"sfx{i}_{k}.wav"
                wav.write_bytes(_sfx(s.get("type", "click")))
                audio.append((t0 + float(s.get("at", 0)), wav, 0.8, False))
            t0 += dur
        base = tmp / "base.mp4"
        warnings += video._finish(parts, tmp, base, with_music, music_volume=0.32 if audio else 0.9,
                                  music_prompt=script.get("music"))
        if audio:
            inputs = ["-i", str(base)]
            fc, labels = "", []
            for n, (st, wav, vol, muffled) in enumerate(audio, 1):
                inputs += ["-i", str(wav)]
                filt = f"[{n}:a]aresample=48000,volume={vol}"
                if muffled:
                    filt += ",lowpass=f=1800,aecho=0.6:0.5:40:0.25"
                fc += f"{filt},adelay={int(st * 1000)}|{int(st * 1000)}[a{n}];"
                labels.append(f"[a{n}]")
            fc += f"[0:a]{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:normalize=0[a]"
            video._run([*inputs, "-filter_complex", fc, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)])
        else:
            out_path.write_bytes(base.read_bytes())
    for i in sorted({0, ci, len(frames) // 2, len(frames) - 1}):
        pictures.append(frames[i])
    return {"warnings": warnings, "cover": cover, "pictures": pictures}
