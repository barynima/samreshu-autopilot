"""Автопилот «Сам решу».

python -m autopilot.main prepare   — подготовить пост дня и прислать в Telegram на проверку
python -m autopilot.main poll      — обработать кнопки и сообщения из Telegram (и опубликовать)
python -m autopilot.main refs      — сгенерировать варианты эталонов Сама и Надо
python -m autopilot.main refresh   — продлить маркер Instagram
python -m autopilot.main stories   — нарисовать сторис для «Актуального» (highlights.yaml)
"""
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import images, instagram, render, telegram, text, video, vposter
from .characters import REF_PROMPTS

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state.json"
POSTS = ROOT / "posts"
REFS = ROOT / "refs"
CANDIDATES = REFS / "candidates"
NAMES = {"sam": "Сам", "nado": "Надо"}


# ---------- состояние и git ----------

def load_state() -> dict:
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding="utf-8"))
        if state.get("pending") and state["pending"] not in {p["id"] for p in load_plan()}:
            state["pending"] = None  # пост из старого плана
            state["awaiting_feedback"] = None
        return state
    return {"offset": 0, "chat_id": None, "done": [], "pending": None, "awaiting_feedback": None}


def save_state(state: dict):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def git_sync(message: str):
    """Сохраняет изменения в репозиторий, чтобы картинки появились по публичной ссылке."""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, check=True)
    run("add", "-A")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    run("commit", "-m", message)
    for _ in range(3):
        if subprocess.run(["git", "push"], cwd=ROOT).returncode == 0:
            return
        run("pull", "--rebase")
    raise RuntimeError("Не удалось сохранить изменения в репозиторий")


def media_url(rel_path: str) -> str:
    """Публичная ссылка на файл через jsDelivr, привязанная к текущему коммиту (правильный тип файла, без кеша)."""
    repo = os.environ["GITHUB_REPOSITORY"]
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return f"https://cdn.jsdelivr.net/gh/{repo}@{sha}/{rel_path}"


def chat(state):
    return os.environ.get("TG_CHAT_ID") or state.get("chat_id")


# ---------- план ----------

def load_plan():
    data = yaml.safe_load((ROOT / "plan.yaml").read_text(encoding="utf-8"))
    return data.get("posts") or data.get("episodes") or []


def next_post(state):
    return next((p for p in load_plan() if p["id"] not in state["done"]), None)


def post_by_id(post_id):
    return next(p for p in load_plan() if p["id"] == post_id)


def load_job(post_id) -> dict:
    return json.loads((POSTS / post_id / "post.json").read_text(encoding="utf-8"))


def save_job(job: dict):
    folder = POSTS / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "post.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")


FORMAT_NAMES = {"reel_scene": "Reels «Сценка»", "reel_pov": "Reels POV", "reel_confess": "Reels «Надо признаётся»",
                "carousel": "Карусель", "reel_text": "Текстовый Reels"}
FRAME_LAYOUT = ("characters in the lower two thirds of the frame with full bodies visible, both clearly visible "
                "and not overlapping; the top quarter of the frame is a plain empty wall (space for captions)")


# ---------- сборка поста ----------

def build_post(item: dict, feedback: str = "") -> dict:
    fmt = item.get("format", "reel_scene")
    script = text.write_post(item, feedback)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    base = f"posts/{item['id']}/{stamp}"
    (POSTS / item["id"]).mkdir(parents=True, exist_ok=True)
    job = {"id": item["id"], "format": fmt, "hook": script["hook"], "caption": script["caption"],
           "script": script, "warnings": []}

    if fmt == "carousel":
        cover = render.carousel_cover(images.frame(script["cover_scene"], "4:5",
                                                   "leave the top third calm and empty for a title"), script["hook"])
        cards = script["cards"]
        n = len(cards) + 2
        blobs = [cover] + [render.text_card(c.get("title", ""), c["text"], f"{i}/{n}")
                           for i, c in enumerate(cards, 2)]
        blobs.append(render.text_card("", script["final"], f"{n}/{n}", final=True))
        job["files"] = []
        for i, b in enumerate(blobs, 1):
            rel = f"{base}_{i}.jpg"
            (ROOT / rel).write_bytes(b)
            job["files"].append(rel)
        job["stage"] = "ready"
        save_job(job)
        return job

    job["end"] = script.get("end", "")
    job["base"] = base

    if fmt == "reel_text":
        job["slides"] = []
        first = None
        for i, sl in enumerate([{"text": script["hook"], "scene": script["first_scene"]}] + script["slides"]):
            rel = f"{base}_slide{i + 1}.png"
            data = images.frame(sl["scene"], "9:16", FRAME_LAYOUT, prev=first)
            first = first or data
            (ROOT / rel).write_bytes(data)
            job["slides"].append({**sl, "image": rel})
        cover = f"{base}_cover.jpg"
        (ROOT / cover).write_bytes(render.reel_cover((ROOT / job["slides"][0]["image"]).read_bytes(), script["hook"]))
        job["cover"] = cover
        assemble(job)
        return job

    job["shots"] = []
    first = None
    for i, shot in enumerate(script["shots"]):
        rel = f"{base}_frame{i + 1}.png"
        data = images.frame(shot["scene"], "9:16", FRAME_LAYOUT, prev=first)
        first = first or data
        (ROOT / rel).write_bytes(data)
        job["shots"].append({**shot, "frame": rel, "clip": None})
    cover = f"{base}_cover.jpg"
    (ROOT / cover).write_bytes(render.reel_cover((ROOT / job["shots"][0]["frame"]).read_bytes(), script["hook"]))
    job["cover"] = cover
    mode = (os.environ.get("VIDEO_MODE") or "syntx").lower()
    if mode == "veo":
        animate_with_veo(job)
    elif mode == "still":
        assemble(job)
    else:
        job["stage"] = "clips"
        save_job(job)
    return job


def animate_with_veo(job):
    for i, shot in enumerate(job["shots"]):
        if shot.get("clip"):
            continue
        frame = (ROOT / shot["frame"]).read_bytes()
        seconds = 8 if job["format"] == "reel_pov" else 6
        rel = f"{job['base']}_clip{i + 1}.mp4"
        (ROOT / rel).write_bytes(video.veo_clip(frame, shot["motion"], seconds))
        shot["clip"] = rel
    assemble(job)


def assemble(job):
    music = (os.environ.get("MUSIC") or "on").lower() != "off"
    if job["format"] == "reel_text":
        slides = [{"image": (ROOT / sl["image"]).read_bytes(), "text": sl["text"]} for sl in job["slides"]]
        rel = f"{job['base']}_reel.mp4"
        job["warnings"] = video.assemble_slides(slides, job.get("end", ""), ROOT / rel, with_music=music)
        job["video"] = rel
        job["stage"] = "ready"
        save_job(job)
        return
    shots = [{"clip": (ROOT / s["clip"]) if s.get("clip") else None,
              "frame": (ROOT / s["frame"]).read_bytes(),
              "speaker": s.get("speaker", ""), "line": s.get("line", "")} for s in job["shots"]]
    rel = f"{job['base']}_reel.mp4"
    voices = (os.environ.get("VOICES") or "off").lower() == "on" and job["format"] != "reel_pov"
    job["warnings"] = video.assemble(shots, job["hook"], ROOT / rel, voices=voices,
                                     end_text=job.get("end", ""), with_music=music)
    job["video"] = rel
    job["stage"] = "ready"
    save_job(job)


# ---------- Telegram: черновики ----------

def send_draft(state, job):
    cid = chat(state)
    pid = job["id"]
    title = f"{FORMAT_NAMES.get(job['format'], '')}: «{job['hook']}»"
    if job.get("stage") == "clips":
        n = len(job["shots"])
        telegram.send_text(cid, f"🎬 {title}\n\nНужно оживить {n} кадр(а) в SYNTX: режим «фото в видео», "
                                "9:16, 5–8 секунд, без звука. Под каждым кадром — промпт для копирования. "
                                "Готовый клип пришлите ОТВЕТОМ на сообщение с кадром.")
        job["clip_msgs"] = {}
        for i, shot in enumerate(job["shots"]):
            prompt = (f"{shot['motion']} Soft matte 3D vinyl toy style, keep the characters exactly as in "
                      f"the image, static camera, no text.")
            m = telegram.send_photo(cid, _to_jpg((ROOT / shot["frame"]).read_bytes()),
                                    f"Кадр {i + 1} из {n}. Промпт:\n\n{prompt}")
            job["clip_msgs"][str(m["message_id"])] = i
        telegram.send_text(cid, "Нет времени на SYNTX?", [
            [{"text": "⚡ Оживить через Veo (≈$1)", "callback_data": f"veo:{pid}"}],
            [{"text": "🖼 Собрать без анимации", "callback_data": f"still:{pid}"}],
            [{"text": "🔁 Другой сценарий", "callback_data": f"redo:{pid}"},
             {"text": "⏭ Пропустить", "callback_data": f"skip:{pid}"}]])
        save_job(job)
        return
    if job["format"] == "carousel":
        telegram.send_album(cid, [(ROOT / f).read_bytes() for f in job["files"]], title)
    else:
        telegram.send_video(cid, (ROOT / job["video"]).read_bytes(), title)
    telegram.send_text(cid, job["caption"][:3900])
    for w in job.get("warnings") or []:
        telegram.send_text(cid, f"ℹ️ {w}"[:3900])
    telegram.send_text(cid, "Публикуем?", [[
        {"text": "✅ Публиковать", "callback_data": f"pub:{pid}"},
        {"text": "🔁 Переделать", "callback_data": f"redo:{pid}"},
    ], [{"text": "⏭ Пропустить", "callback_data": f"skip:{pid}"}]])


def receive_clip(state, msg):
    cid = chat(state)
    job = load_job(state["pending"])
    media = msg.get("video") or msg.get("animation") or msg.get("document")
    reply = str((msg.get("reply_to_message") or {}).get("message_id", ""))
    idx = (job.get("clip_msgs") or {}).get(reply)
    if idx is None:
        idx = next((i for i, s in enumerate(job["shots"]) if not s.get("clip")), None)
    if idx is None:
        telegram.send_text(cid, "Все клипы уже есть 👍")
        return
    rel = f"{job['base']}_clip{idx + 1}.mp4"
    (ROOT / rel).write_bytes(telegram.download(media["file_id"]))
    job["shots"][idx]["clip"] = rel
    missing = [i + 1 for i, s in enumerate(job["shots"]) if not s.get("clip")]
    save_job(job)
    if missing:
        telegram.send_text(cid, f"Кадр {idx + 1} принят ✅ Жду ещё: {', '.join(map(str, missing))}")
        return
    telegram.send_text(cid, "Все клипы на месте. Собираю ролик с субтитрами и голосами, пара минут…")
    assemble(job)
    send_draft(state, job)


def publish(state, post_id):
    job = load_job(post_id)
    git_sync(f"Файлы поста {post_id}")
    time.sleep(20)
    if job["format"] == "carousel":
        link = instagram.publish_carousel([media_url(f) for f in job["files"]], job["caption"])
    else:
        link = instagram.publish_reel(ROOT / job["video"], job["caption"])
    if vposter.enabled():
        try:
            if job["format"] == "carousel":
                where = vposter.publish(job["caption"], images=[ROOT / f for f in job["files"]])
            else:
                where = vposter.publish(job["caption"], video=ROOT / job["video"])
            link += "\nТакже отправлено: " + ", ".join(where) + " (выйдет примерно через минуту)."
        except Exception as e:
            link += f"\n⚠️ ВКонтакте не получилось: {e}"[:1500]
    state["done"].append(post_id)
    state["pending"] = None
    state["awaiting_feedback"] = None
    cleanup_media(post_id)
    return link


def cleanup_media(post_id):
    """Картинки и видео опубликованного или пропущенного поста больше не нужны: держим репозиторий лёгким
    (jsDelivr не отдаёт файлы из репозитория больше 50 МБ, а через него идут картинки каруселей)."""
    folder = POSTS / post_id
    if not folder.exists():
        return
    for f in folder.iterdir():
        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".mp4", ".wav"):
            f.unlink()
    git_sync(f"Убраны файлы поста {post_id}")


def cmd_prepare(state):
    process(state, telegram.updates(state["offset"]))  # сначала то, что накопилось (выбор эталонов и т.п.)
    _prepare(state)
    if chat(state) and state.get("pending"):
        session(state, 60)  # ждём решения или клипов час, каждое действие продлевает


def _prepare(state):
    if not chat(state):
        print("Чат с ботом ещё не найден: напишите боту в Telegram любое сообщение.")
        return
    if state.get("pending"):
        telegram.send_text(chat(state), "Напоминаю: предыдущий пост ждёт решения 👆")
        return
    item = next_post(state)
    if not item:
        telegram.send_text(chat(state), "Контент-план закончился. Допишите посты в plan.yaml.")
        return
    job = build_post(item)
    state["pending"] = item["id"]
    if job.get("stage") == "ready" and os.environ.get("AUTO_PUBLISH", "").lower() == "true":
        telegram.send_text(chat(state), f"Опубликовано: {publish(state, item['id'])}")
    else:
        send_draft(state, job)


# ---------- эталоны ----------

def cmd_refs(state, count=3):
    CANDIDATES.mkdir(parents=True, exist_ok=True)
    cid = chat(state)
    for key, prompt in REF_PROMPTS.items():
        files = []
        for i in range(1, count + 1):
            data = images.reference_sheet(prompt)
            path = CANDIDATES / f"{key}_{i}.png"
            import io
            from PIL import Image
            Image.open(io.BytesIO(data)).save(path, "PNG")
            files.append(_to_jpg(data))
        if cid:
            telegram.send_album(cid, files, f"{NAMES[key]}: варианты 1–{count}")
            telegram.send_text(cid, f"Какой {NAMES[key]} — эталон?", [[
                {"text": f"{NAMES[key]} {i}", "callback_data": f"ref:{key}:{i}"} for i in range(1, count + 1)
            ]])
    if cid:
        telegram.send_text(cid, "Не нравится ни один?", [[{"text": "🔁 Нарисовать заново", "callback_data": "refs:all"}]])


def _to_jpg(data: bytes) -> bytes:
    import io
    from PIL import Image
    out = io.BytesIO()
    Image.open(io.BytesIO(data)).convert("RGB").save(out, "JPEG", quality=90)
    return out.getvalue()


# ---------- сторис для «Актуального» ----------

HIGHLIGHTS = ROOT / "highlights.yaml"
STORY_LAYOUT = ("characters in the lower 60% of the frame; keep the top 35% calm, plain and empty "
                "for a big text block; no text, letters or numbers anywhere except where the scene asks for them")


def cmd_stories(state, only: str = ""):
    import zipfile
    import io
    cid = chat(state)
    data = yaml.safe_load(HIGHLIGHTS.read_text(encoding="utf-8"))["highlights"]
    if only:
        data = [h for h in data if h["id"] == only]
        if not data and cid:
            ids = ", ".join(h["id"] for h in yaml.safe_load(HIGHLIGHTS.read_text(encoding="utf-8"))["highlights"])
            telegram.send_text(cid, f"Нет такого хайлайта. Есть: {ids}")
            return
    for h in data:
        folder = POSTS / "stories" / h["id"]
        folder.mkdir(parents=True, exist_ok=True)
        files, notes = [], []
        for i, s in enumerate(h["stories"], 1):
            if s.get("scene"):
                pic = render.story(images.frame(s["scene"], "9:16", STORY_LAYOUT), s["text"])
            else:
                pic = render.story_card(s["text"], s.get("bg", "coral"))
            (folder / f"{i:02d}.jpg").write_bytes(pic)
            files.append(pic)
            if s.get("sticker"):
                notes.append(f"Сторис {i}: добавьте стикер — {s['sticker']}")
        if not cid:
            continue
        for start in range(0, len(files), 10):
            telegram.send_album(cid, files[start:start + 10], f"«{h['title']}»: {len(files)} сторис по порядку")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for i, pic in enumerate(files, 1):
                z.writestr(f"{h['id']}-{i:02d}.jpg", pic)
        telegram.send_document(cid, buf.getvalue(), f"{h['id']}.zip", "Файлы в полном качестве — для загрузки в сторис")
        tips = "\n".join(notes) if notes else "Стикеры не нужны."
        telegram.send_text(cid, f"Как выложить «{h['title']}»: опубликуйте сторис по порядку, затем "
                                f"«Добавить в актуальное» → «{h['title']}».\n{tips}\n"
                                f"Не нравится? /stories {h['id']} — нарисую заново.")


# ---------- проба визуала ----------

PROBE = [
    ("Мама ночью в родительском чате",
     "Late evening in a modern renovated apartment kitchen. A woman about 42 sits at the table under a warm lamp, "
     "looking at her phone with a tired, worried face; a cup of tea gone cold, school notebooks on the table."),
    ("Закрытая дверь и «не знаю»",
     "View from a dim hallway into a teenager's room through a half-open door: a teenager in a grey hoodie with the hood "
     "up sits on a neatly made bed in a modern teen room wearing big headphones, back to the camera, face not visible, lit by a phone screen; "
     "a school backpack on the floor next to a desk with a gaming PC."),
    ("Разговор без ссоры",
     "A modern bright kitchen-living room at dinner time. A mother about 45 and a teenager in a hoodie sit at the table; the "
     "teenager is seen from behind over the shoulder, face not visible. The mother leans in, smiling gently, listening."),
]


def cmd_probe(state):
    cid = chat(state)
    pics = []
    for title, scene in PROBE:
        img = images.life(scene)
        pics.append(render.reel_cover(img, title))
    telegram.send_album(cid, pics, "Проба визуала «из жизни»: 3 кадра для мам 35–50. Что цепляет, что нет?")


# ---------- Telegram ----------

def process(state, ups):
    for up in ups:
        state["offset"] = up["update_id"] + 1
        try:
            handle(state, up)
        except Exception as e:  # сообщаем об ошибке и идём дальше
            if chat(state):
                telegram.send_text(chat(state), f"⚠️ Ошибка: {e}"[:3900])
            print("Ошибка:", e, file=sys.stderr)
        save_state(state)
    if ups:
        git_sync("Автопилот: ответы из Telegram")


def session(state, minutes: float):
    """Слушаем Telegram и сразу отвечаем на кнопки. Каждое действие продлевает сеанс на 20 минут."""
    deadline = time.time() + minutes * 60
    while True:
        left = deadline - time.time()
        ups = telegram.updates(state["offset"], wait=int(max(0, min(50, left))))
        if ups:
            print(f"Сообщений и нажатий: {len(ups)}")
            process(state, ups)
            deadline = min(max(deadline, time.time() + 20 * 60), time.time() + 90 * 60)
        elif left <= 0:
            return


def cmd_poll(state):
    if not chat(state):
        me = telegram._api("getMe")
        hook = telegram._api("getWebhookInfo")
        print(f"Бот: @{me.get('username')} ({me.get('first_name')})")
        if hook.get("url"):
            print("У бота был включён вебхук — отключаю, иначе сообщения не доходят.")
            telegram._api("deleteWebhook", json={"drop_pending_updates": False})
    session(state, float(os.environ.get("SESSION_MINUTES") or 30))


def handle(state, up):
    msg = up.get("message")
    cb = up.get("callback_query")
    src = (msg or {}).get("chat") or ((cb or {}).get("message") or {}).get("chat") or {}
    if not state.get("chat_id") and src.get("type") == "private":
        state["chat_id"] = src["id"]
        telegram.send_text(src["id"], "Привет! Я автопилот «Сам решу». Буду присылать сюда посты на проверку.\n"
                                      "Команды: /post — сделать пост сейчас, /refs — нарисовать эталоны персонажей, "
                                      "/stories — сторис для «Актуального».")
        return
    if str(src.get("id")) != str(chat(state)):
        return  # чужие чаты игнорируем
    cid = chat(state)

    if cb:
        telegram.answer(cb["id"])
        kind, _, arg = cb["data"].partition(":")
        if kind in ("pub", "redo", "again", "skip", "veo", "still") and state.get("pending") != arg:
            telegram.send_text(cid, "Эта кнопка от старого поста, он уже не актуален. "
                                    "Отправьте /post — сделаю следующий пост по новому плану.")
            return
        if kind == "pub" and state.get("pending") == arg:
            telegram.send_text(cid, "Публикую… ⏳")
            telegram.send_text(cid, f"Готово! {publish(state, arg)}")
        elif kind == "redo" and state.get("pending") == arg:
            state["awaiting_feedback"] = arg
            telegram.send_text(cid, "Напишите одним сообщением, что поправить. Или нажмите кнопку — сделаю другой вариант.",
                               [[{"text": "🎲 Просто другой вариант", "callback_data": f"again:{arg}"}]])
        elif kind == "again" and state.get("pending") == arg:
            regenerate(state, arg, "")
        elif kind in ("veo", "still") and state.get("pending") == arg:
            job = load_job(arg)
            if job.get("stage") != "clips":
                return
            telegram.send_text(cid, "Оживляю через Veo, 3–6 минут…" if kind == "veo" else "Собираю ролик из кадров…")
            animate_with_veo(job) if kind == "veo" else assemble(job)
            send_draft(state, job)
        elif kind == "skip" and state.get("pending") == arg:
            state["done"].append(arg)
            state["pending"] = None
            state["awaiting_feedback"] = None
            cleanup_media(arg)
            telegram.send_text(cid, "Пропустили. Следующий пост — завтра утром (или /post прямо сейчас).")
        elif kind == "ref":
            key, _, num = arg.partition(":")
            shutil.copy(CANDIDATES / f"{key}_{num}.png", REFS / f"{key}.png")
            telegram.send_text(cid, f"Эталон «{NAMES[key]}» сохранён ✅")
            git_sync(f"Эталон {key}")
            if state.get("pending") and (REFS / "sam.png").exists() and (REFS / "nado.png").exists():
                telegram.send_text(cid, "Оба эталона выбраны. Перерисовать текущий пост уже с ними?",
                                   [[{"text": "🎨 Перерисовать", "callback_data": f"again:{state['pending']}"}]])
        elif kind == "refs":
            telegram.send_text(cid, "Рисую новые варианты, это займёт пару минут…")
            cmd_refs(state)
        return

    media = msg.get("video") or msg.get("animation") or msg.get("document")
    if media and state.get("pending"):
        if (POSTS / state["pending"] / "post.json").exists() and load_job(state["pending"]).get("stage") == "clips":
            receive_clip(state, msg)
        else:
            telegram.send_text(cid, "Сейчас клипы не нужны — ролик уже собран 🙂")
        return

    body = (msg.get("text") or "").strip()
    if body.startswith("/post"):
        if state.get("pending"):
            telegram.send_text(cid, "Сначала решите судьбу предыдущего поста 👆")
        else:
            telegram.send_text(cid, "Готовлю пост, это займёт 2–4 минуты…")
            _prepare(state)
    elif body.startswith("/rebuild"):
        pid = state.get("pending")
        if not pid or not (POSTS / pid / "post.json").exists() or load_job(pid)["format"] == "carousel":
            telegram.send_text(cid, "Сейчас нечего пересобирать.")
        else:
            job = load_job(pid)
            telegram.send_text(cid, "Пересобираю ролик из готовых кадров и клипов, пара минут…")
            job["cover"] = f"{job['base']}_cover.jpg"
            first = (ROOT / (job["shots"][0]["frame"] if job.get("shots") else job["slides"][0]["image"])).read_bytes()
            (ROOT / job["cover"]).write_bytes(render.reel_cover(first, job["hook"]))
            assemble(job)
            send_draft(state, job)
    elif body.startswith("/probe"):
        telegram.send_text(cid, "Рисую 3 пробных кадра в стиле «из жизни», пара минут…")
        cmd_probe(state)
    elif body.startswith("/stories"):
        only = body[len("/stories"):].strip()
        telegram.send_text(cid, "Рисую сторис для «Актуального», это займёт 5–10 минут…\n"
                                "По одному хайлайту: /stories chto-eto, kto-ya, roditelyam или oge")
        cmd_stories(state, only)
    elif body.startswith("/refs"):
        telegram.send_text(cid, "Рисую эталоны персонажей, это займёт пару минут…")
        cmd_refs(state)
    elif state.get("awaiting_feedback") and body:
        regenerate(state, state["awaiting_feedback"], body)


def regenerate(state, ep_id, feedback):
    telegram.send_text(chat(state), "Переделываю, это займёт 2–4 минуты…")
    job = build_post(post_by_id(ep_id), feedback)
    state["awaiting_feedback"] = None
    send_draft(state, job)


# ---------- маркер Instagram ----------

def cmd_refresh(state):
    token = instagram.refresh_token()
    pat, repo = os.environ.get("GH_PAT"), os.environ.get("GITHUB_REPOSITORY")
    if pat and repo:
        update_github_secret(repo, pat, "IG_ACCESS_TOKEN", token)
        print("Маркер продлён и сохранён в секретах.")
    elif chat(state):
        telegram.send_text(chat(state), "Маркер Instagram продлён, но сохранить его автоматически нельзя: "
                                        "не задан секрет GH_PAT. Обновите IG_ACCESS_TOKEN вручную в течение 60 дней.")


def update_github_secret(repo, pat, name, value):
    import base64
    import requests
    from nacl import encoding, public
    h = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    key = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=h, timeout=30).json()
    box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    enc = base64.b64encode(box.encrypt(value.encode())).decode()
    r = requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/{name}", headers=h, timeout=30,
                     json={"encrypted_value": enc, "key_id": key["key_id"]})
    r.raise_for_status()


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "poll"
    state = load_state()
    try:
        {"prepare": cmd_prepare, "poll": cmd_poll, "refs": cmd_refs, "refresh": cmd_refresh,
         "stories": cmd_stories}[cmd](state)
    except Exception as e:
        if chat(state):
            try:
                telegram.send_text(chat(state), f"⚠️ Автопилот ({cmd}): {e}"[:3900])
            except Exception:
                pass
        raise
    finally:
        save_state(state)
        git_sync(f"Автопилот: {cmd}")


if __name__ == "__main__":
    main()
