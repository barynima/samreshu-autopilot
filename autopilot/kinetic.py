"""Reels «Кинетический текст»: слайды движка сторис оживают — счётчик цифры, зачёркивание мифа,
пункты появляются по одному. Кадр из жизни — только на первом слайде (крючок)."""
import io
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from . import stories, video

FPS = 30
W, H = 1080, 1920


def _im(b):
    return Image.open(io.BytesIO(b)).convert("RGB")


def _ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _read(text: str) -> float:
    """Сколько держать слайд: ~2.5 слова в секунду, но не меньше 1.8 и не больше 3.5 с."""
    words = len(re.sub(r"[*«»]", "", text).split())
    return max(1.8, min(3.5, words / 2.5 + 0.6))


class _Reel:
    def __init__(self):
        self.frames = []

    def hold(self, img, sec):
        self.frames += [img] * int(sec * FPS)

    def fade(self, a, b, sec, rise=0):
        n = max(1, int(sec * FPS))
        for i in range(n):
            t = _ease((i + 1) / n)
            if rise:
                off = int(rise * (1 - t))
                sh = b.copy()
                if off:
                    sh = Image.new("RGB", (W, H))
                    sh.paste(b, (0, off))
                    sh.paste(b.crop((0, 0, W, off)), (0, 0))
                self.frames.append(Image.blend(a, sh, t))
            else:
                self.frames.append(Image.blend(a, b, t))

    def wipe(self, a, b, sec, axis="x", start=0, end=None):
        n = max(1, int(sec * FPS))
        end = end or (W if axis == "x" else H)
        for i in range(n):
            pos = int(start + (end - start) * _ease((i + 1) / n))
            m = Image.new("L", (W, H), 0)
            ImageDraw.Draw(m).rectangle((0, 0, pos, H) if axis == "x" else (0, 0, W, pos), fill=255)
            f = a.copy()
            f.paste(b, (0, 0), m)
            self.frames.append(f)


def _number_steps(number: str):
    """'65%' → ['0%', '5%', ..., '65%']; не число — без счётчика."""
    m = re.match(r"^(\D*)(\d+)(\D*)$", number.strip())
    if not m:
        return [number]
    pre, val, post = m.group(1), int(m.group(2)), m.group(3)
    steps = 12
    return [f"{pre}{round(val * k / steps)}{post}" for k in range(steps + 1)]


def render(script: dict, photo: bytes, kicker: str, out_path: Path, with_music: bool = True) -> dict:
    """Собирает ролик. Возвращает {"warnings": [...], "cover": jpeg, "pictures": [jpeg...]} — кадры для критика."""
    stories.canvas(False)
    r = _Reel()
    pics = []
    hero = {"type": "hero", "title": script["hook"], "sub": script.get("hook_sub", "")}
    hero_full = _im(stories.slide(hero, kicker, "", photo))
    hero_base = _im(stories.slide({"type": "hero", "title": " ", "sub": " "}, kicker, "", photo))
    cover = stories.slide(hero, kicker, "", photo)
    pics.append(cover)
    r.hold(hero_base, 0.3)
    r.fade(hero_base, hero_full, 0.6, rise=60)
    r.hold(hero_full, _read(script["hook"]))
    prev = hero_full

    for s in script["slides"]:
        kind = s["type"]
        full = _im(stories.slide(s, kicker, ""))
        pics.append(stories.slide(s, kicker, ""))
        if kind == "number":
            steps = [_im(stories.slide({**s, "number": v, "text": " ", "note": ""}, kicker, ""))
                     for v in _number_steps(s["number"])]
            r.fade(prev, steps[0], 0.35)
            for st in steps:
                r.hold(st, 0.07)
            r.fade(steps[-1], full, 0.5, rise=40)
            r.hold(full, _read(s["text"] + " " + s.get("note", "")))
        elif kind == "myth":
            m0 = _im(stories.slide({**s, "_nostrike": True, "_ghost": True}, kicker, ""))
            m1 = _im(stories.slide({**s, "_ghost": True}, kicker, ""))
            r.fade(prev, m0, 0.35)
            r.hold(m0, 1.0)
            r.wipe(m0, m1, 0.6, "x", 60, W - 60)
            r.hold(m1, 0.4)
            r.fade(m1, full, 0.5, rise=40)
            r.hold(full, _read(s["truth"]))
        elif kind == "list":
            base = _im(stories.slide({**s, "_ghost": True}, kicker, ""))
            r.fade(prev, base, 0.3)
            r.wipe(base, full, 0.8 * len(s["items"]), "y", 600, 1560)
            r.hold(full, 1.6)
        else:  # qa и прочее — всплытие
            r.fade(prev, full, 0.45, rise=50)
            r.hold(full, _read(s.get("q", "") + " " + s.get("a", "")))
        prev = full

    end = _im(stories.slide({"type": "cta", "title": script["end"], "sub": script.get("end_sub", "")}, kicker, ""))
    r.fade(prev, end, 0.45, rise=50)
    r.hold(end, 2.6)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        silent = tmp / "v.mp4"
        p = subprocess.Popen([video.ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                              "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", *video.SILENCE, "-shortest",
                              *video.ENC, str(silent)], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        for f in r.frames:
            p.stdin.write(f.tobytes())
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg: {p.stderr.read().decode()[-400:]}")
        warnings = video._finish([silent], tmp, out_path, with_music)
    return {"warnings": warnings, "cover": cover, "pictures": pics}
