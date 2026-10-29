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

from . import images, production, render, text, video, yandex_tts

W, H = 1080, 1920
FPS = 30
ACCENT = (255, 138, 61)  # тёплый оранжевый, как в сценарии SYNTX
FONTS = Path(__file__).resolve().parent.parent / "fonts"

CINE_STYLE = ("Photorealistic still from a modern Russian-language family drama series, shot on a cinema camera, 35mm. "
              "Follow the location and family references exactly within this episode. Everyday believable homes, "
              "varied interiors and ordinary clothes, never a generic luxury apartment unless requested. "
              "Natural readable light, emotions appropriate to the action, no forced smiles or melodramatic gloom. "
              "ANATOMY PERFECT: every person has exactly two arms, two hands, five fingers; "
              "people sit firmly and stand on the floor; one continuous room with consistent walls and ceiling. "
              "Vertical 9:16, no text, no letters, no captions, no watermark, no logos.")
CINE_MOTION = "Realistic motion, no morphing, stable faces, no text appearing, static or very slow camera."


def motion_instruction(shot):
    instruction = f"{shot.get('motion', '')} {CINE_MOTION}"
    if "door" in (shot.get("visual", "") + " " + shot.get("motion", "")).lower():
        instruction += (" The door's hinge edge stays fixed to the frame. Only the opposite handle edge moves. "
                        "Preserve hinge side, handle position, rigid door geometry and physical opening direction.")
    return instruction


def _f(size, w="ExtraBold"):
    name = {"regular": "SemiBold", "bold": "ExtraBold", "black": "Black"}.get(w, w)
    return ImageFont.truetype(str(FONTS / f"Nunito-{name}.ttf"), size)


# ---------- надписи ----------

def _split_accent(t):
    import re
    out = []
    for i, part in enumerate(re.split(r"\*", t)):
        for w in part.split(" "):
            if not w:
                continue
            if out and w[0] in ",.!?:;»)…" and not part.startswith(" "):
                out.append(("\x00" + w, i % 2 == 1))  # прилепить к предыдущему слову без пробела
            else:
                out.append((w, i % 2 == 1))
    merged = []
    for w, a in out:
        if w.startswith("\x00") and merged:
            merged.append((w[1:], a, True))
        else:
            merged.append((w, a, False))
    return merged


def _wrap(tokens, font, max_w):
    lines, cur, cw = [], [], 0
    sp = font.getlength(" ")
    for w, a, glue in tokens:
        ww = font.getlength(w)
        if glue and cur:  # знак препинания — к предыдущему слову, без пробела
            cur.append((w, a, ww, True))
            cw += ww
            continue
        if cur and cw + sp + ww > max_w:
            lines.append(cur)
            cur, cw = [], 0
        cur.append((w, a, ww, False))
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
            gaps = sum(1 for k, t in enumerate(line) if k and not t[3])
            lw = sum(t[2] for t in line) + sp * gaps
            x = (W - lw) / 2
            for k, (w, a, ww, glue) in enumerate(line):
                if k and not glue:
                    x += sp
                sd.text((x + 2, y + 4), w, font=f, fill=(0, 0, 0, 150))
                d.text((x, y), w, font=f, fill=ACCENT + (255,) if a else (255, 255, 255, 255))
                x += ww
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


def _voice(line: str, teen: bool, warnings: list) -> bytes:
    """Подросток — Google (его голос устраивает). Мама — Яндекс, если есть ключ; при сбое — Google."""
    if not teen and yandex_tts.enabled():
        try:
            return yandex_tts.synth(line)
        except Exception as e:  # noqa: BLE001
            if not any("Яндекс" in w for w in warnings):
                warnings.append(f"Голос мамы от Google: {str(e)[:150]}")
    return _tts(line, TEEN if teen else NARRATOR, rate=1.0 if teen else 0.92)


# ---------- кадры ----------

def _shot_image(shot, refs: dict, allow_face=True):
    """Кадр по опорным изображениям; до 3 попыток с проверкой на брак. Возвращает (bytes|None, отчёт)."""
    import base64
    best = (None, None)
    for attempt in range(3):
        production.progress(f"Проверка кадра: попытка {attempt + 1}/3")
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
        rep = text.check_photo(img, shot["visual"], allow_teen_face=allow_face,
                               references={rid: refs[rid] for rid in shot.get("refs", []) if rid in refs})
        if rep["ok"]:
            return img, rep
        if not best[1] or rep["score"] > best[1].get("score", 0):
            bad = any(w in p.lower() for p in rep["problems"] for w in ("рук", "пальц", "букв", "надпис", "текст"))
            best = (img if rep["score"] >= 6 and not bad else None, rep)
    return None, best[1]


def _jpg(data: bytes) -> bytes:
    pic = render.fit_vertical(data)
    out = io.BytesIO()
    pic.save(out, "JPEG", quality=93)
    return out.getvalue()


def _cached(folder, name, create):
    path = folder / name
    if path.exists():
        return path.read_bytes()
    data = create()
    if not data:
        raise RuntimeError(f"Этап {name} не вернул данные")
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(path)
    return data


def _voice_duration(shot, wav_bytes):
    with wave.open(io.BytesIO(wav_bytes)) as stream:
        length = stream.getnframes() / stream.getframerate()
    required = float((shot.get("vo") or {}).get("at", 0.2)) + length + 0.25
    if required > 8:
        raise RuntimeError("Реплика длиннее 8 секунд: сократите её перед сборкой")
    return max(float(shot.get("sec") or 3), required)


def render_reel(script: dict, out_path: Path, with_music=True, veo=False, cache_dir=None, family=None) -> dict:
    """Собирает ролик. Возвращает {"warnings", "cover", "pictures"}."""
    warnings, pictures = [], []
    cache_dir = Path(cache_dir) if cache_dir else out_path.parent / "generation" / production.fingerprint(script)[:20]
    cache_dir.mkdir(parents=True, exist_ok=True)
    family_note = production.family_instruction(family) if family else ""
    refs = {}
    for c in script.get("cast") or []:
        production.progress(f"Герои и локации: {c['id']}")
        def reference(c=c):
            for _ in range(3):
                img = _jpg(images._call([{"text": f"{c['prompt']}\n{CINE_STYLE}\n{family_note}"}], "9:16"))
                rep = text.check_photo(img, c["prompt"], allow_teen_face=True)
                if rep["ok"]:
                    return img
            raise RuntimeError(f"Эталон {c['id']} не прошёл проверку: {rep.get('problems')}")
        key = production.fingerprint(c)[:16]
        refs[c["id"]] = _cached(cache_dir, f"ref-{key}.jpg", reference)
    voices = {}
    timeline = []
    total = 0.0
    for i, shot in enumerate(script["shots"]):
        dur = float(shot.get("sec") or 3)
        vo = shot.get("vo")
        if vo and vo.get("text"):
            production.progress(f"Озвучка: реплика {i + 1}/{len(script['shots'])}")
            key = production.fingerprint(vo)[:16]
            voices[i] = _cached(cache_dir, f"voice-{key}.wav", lambda vo=vo: _voice(vo["text"], vo.get("voice") == "teen", warnings))
            dur = _voice_duration(shot, voices[i])
        timeline.append({"shot": i + 1, "start": round(total, 3), "duration": round(dur, 3)})
        total += dur
    if total > 50:
        raise RuntimeError(f"После подгонки речи фильм длится {total:.1f} с — сократите сценарий до 50 с")
    frames = []
    for i, shot in enumerate(script["shots"], 1):
        production.progress(f"Кадр {i}/{len(script['shots'])}: генерация и проверка")
        def frame(shot=shot, i=i):
            img, rep = _shot_image(shot, refs)
            if img is None:
                raise RuntimeError(f"Кадр {i} не соответствует сценарию: {(rep or {}).get('problems')}. "
                                   "Случайная замена не используется")
            return _jpg(img)
        frames.append(_cached(cache_dir, f"frame-{i}.jpg", frame))
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
            production.progress(f"Монтаж сцены {i + 1}/{len(frames)}")
            dur = timeline[i]["duration"]
            pic = tmp / f"f{i}.png"
            Image.open(io.BytesIO(frame)).save(pic)
            clip = None
            if veo:
                vsec = 4 if dur <= 4 else 6 if dur <= 6 else 8  # Veo умеет только 4, 6 и 8 секунд
                def animate():
                    data = video.veo_clip(frame, motion_instruction(shot), vsec,
                                          style=video.LIFE_VIDEO, models=video.VEO_FAST)
                    problem = video.clip_problem(data, frame, vsec)
                    if problem:
                        raise RuntimeError(f"Движение кадра {i + 1} не прошло проверку: {problem}")
                    production.progress(f"Физика движения сцены {i + 1}/{len(frames)}")
                    text.check_motion(data, frame, shot)
                    return data
                clip = tmp / f"c{i}.mp4"
                clip.write_bytes(_cached(cache_dir, f"motion-checked-{i}.mp4", animate))
            layers = []
            for j, b in enumerate(shot.get("texts") or []):
                png = tmp / f"t{i}_{j}.png"
                text_layer(b).save(png)
                end = float(b.get("to", dur))
                if end >= float(shot.get("sec") or 3):
                    end = dur
                layers.append((png, float(b.get("at", 0)), end))
            out = tmp / f"p{i}.mp4"
            video._still_multi(pic, layers, dur, out, zoom=True, clip=clip)
            parts.append(out)
            vo = shot.get("vo")
            if vo and vo.get("text"):
                wav = tmp / f"vo{i}.wav"
                teen = vo.get("voice") == "teen"
                wav.write_bytes(voices[i])
                audio.append((t0 + float(vo.get("at", 0.2)), wav, 1.6 if not teen else 1.2, teen))
            for k, s in enumerate(shot.get("sfx") or []):
                wav = tmp / f"sfx{i}_{k}.wav"
                wav.write_bytes(_sfx(s.get("type", "click")))
                audio.append((t0 + float(s.get("at", 0)), wav, 0.8, False))
            t0 += dur
        # голоса не должны наезжать друг на друга: если реплика начинается раньше, чем кончилась предыдущая, сдвигаем
        voices = sorted([a for a in audio if a[2] >= 1.2], key=lambda a: a[0])
        last_end = 0.0
        for k, (st, wav, vol, muffled) in enumerate(voices):
            try:
                with wave.open(str(wav)) as wv:
                    length = wv.getnframes() / wv.getframerate()
            except Exception:  # noqa: BLE001
                length = 2.0
            if st < last_end + 0.15:
                audio[audio.index(voices[k])] = (last_end + 0.15, wav, vol, muffled)
                st = last_end + 0.15
            last_end = st + length
        if last_end > t0:
            raise RuntimeError("Озвучка не укладывается в фильм; экспорт остановлен вместо обрезания речи")
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
    if abs(video.duration(out_path) - total) > 0.5:
        raise RuntimeError("Длительность экспорта не совпадает с монтажной шкалой")
    production.progress("Фильм собран: проверяю финальное видео со звуком")
    for i in range(len(frames)):
        pictures.append(frames[i])
    return {"warnings": warnings, "cover": cover, "pictures": pictures, "timeline": timeline}

