"""Local assembly checks. All external network calls are prohibited."""
import copy
import io
import json
import os
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw
from autopilot import main, text, video, cinematic

OUT = Path(__file__).resolve().parent / "smoke-output"
OUT.mkdir(exist_ok=True)
main.ROOT = OUT
main.POSTS = OUT / "posts"
main.STATE = OUT / "state.json"
pic = Image.new("RGB", (1080, 1920), "#fff0df")
ImageDraw.Draw(pic).rectangle((90, 1000, 990, 1600), fill="#ffc0a0")
buf = io.BytesIO()
pic.save(buf, "JPEG")
PHOTO = buf.getvalue()

def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected network call during local smoke check")

fixtures = {
    "reel_kinetic": {"hook": "Разговор без давления", "caption": "Один небольшой шаг.",
        "first_scene": "Test scene", "end": "Сначала выслушайте",
        "slides": [{"type": "qa", "q": "С чего начать?", "a": "С интересов."},
                   {"type": "qa", "q": "Что дальше?", "a": "Попробовать вместе."}]},
    "reel_scene": {"hook": "Разговор без давления", "caption": "Один небольшой шаг.",
        "end": "Сначала выслушайте", "shots": [{"speaker": "Сам", "line": "Я подумаю",
        "scene": "Test scene", "motion": "Small movement"} for _ in range(3)]},
    "carousel": {"hook": "Ребёнок отвечает: не знаю", "caption": "Попробуйте начать с интересов.",
        "cover_scene": "Test scene", "cards": [
            {"type": "qa", "q": "Что ему интересно?", "a": "Начните с того, о чём он говорит сам."},
            {"type": "myth", "myth": "Нужно выбрать навсегда", "truth": "Можно исследовать направления."},
            {"type": "list", "title": "Один шаг", "items": ["Выслушать", "Задать вопрос"]}],
        "final": {"title": "Поговорите без давления", "sub": "Сам решу"}},
    "reel_chat": {"hook": "Не знаю", "caption": "Попробуйте другой вопрос.", "first_scene": "Test scene",
        "dialog": [{"who": "mom", "text": "Что тебе интересно?"}, {"who": "sam", "text": "Пока не знаю"}],
        "slides": [{"text": "Начните с того, что уже нравится."}, {"text": "Выслушайте без оценки."},
                   {"text": "Предложите один небольшой шаг."}], "end": "Сохраните вопросы"},
    "reel_story": {"hook": "Разговор без давления", "caption": "Один шаг навстречу.",
        "cast": [{"id": "mom", "prompt": "Test mom"}],
        "shots": [{"sec": 1, "refs": ["mom"], "visual": "Test scene", "texts": [{"at": 0, "to": 1, "pos": "top",
                   "lines": [{"t": "Сначала выслушать", "w": "bold"}]}]} for _ in range(6)],
        "cover_shot": 1, "cover_lines": [{"t": "Поговорите спокойно", "w": "bold"}]}
}

results = []
with patch.dict(os.environ, {"MUSIC": "off", "VOICES": "off", "CINE_VEO": "off", "VIDEO_MODE": "still"}), \
     patch("requests.sessions.Session.request", side_effect=forbidden), \
     patch.object(main.images, "life_checked", return_value=(PHOTO, {"ok": True})), \
     patch.object(main.images, "_call", return_value=PHOTO), \
     patch.object(main.images, "frame", return_value=PHOTO), \
     patch.object(text, "check_photo", return_value={"ok": True}), \
     patch.object(text, "check_storyboard", return_value={"critical": [], "fixes": []}), \
     patch.object(main.kinetic, "FPS", 5), \
     patch.object(cinematic, "_shot_image", return_value=(PHOTO, {"ok": True})):
    for fmt, script in fixtures.items():
        if os.environ.get("ONLY_FORMATS") and fmt not in os.environ["ONLY_FORMATS"].split(","):
            continue
        validated = text._validate(fmt, copy.deepcopy(script))
        with patch.object(text, "write_post", return_value=validated):
            job = main.build_post({"id": "smoke-" + fmt, "format": fmt})
        assert job["stage"] == "ready"
        assert main.load_job(job["id"])["caption"] == script["caption"]
        if fmt == "carousel":
            sizes = [Image.open(OUT / f).size for f in job["files"]]
            assert len(sizes) == 5 and all(s == (1080, 1350) for s in sizes), sizes
            record = {"format": fmt, "slides": len(sizes), "dimensions": sizes[0]}
        else:
            path = OUT / job["video"]
            length = video.duration(path)
            assert path.stat().st_size > 1000 and length >= 5
            record = {"format": fmt, "duration_seconds": length, "bytes": path.stat().st_size}
        main.archive_draft(job)
        results.append(record)
        print(json.dumps(record), flush=True)
(OUT / "results.json").write_text(json.dumps(results, indent=2))
