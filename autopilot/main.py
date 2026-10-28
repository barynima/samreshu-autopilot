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

import requests
import yaml

from . import cinematic, images, instagram, kinetic, planner, render, stories, storage, telegram, text, video, vposter
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
        # При конфликте (обычно state.json) побеждают изменения этого запуска: бот — главный источник состояния.
        if subprocess.run(["git", "pull", "--rebase", "-X", "theirs"], cwd=ROOT).returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=ROOT)
            subprocess.run(["git", "pull", "--no-rebase", "-X", "ours", "--no-edit"], cwd=ROOT)
    raise RuntimeError("Не удалось сохранить изменения в репозиторий")


def public_url(rel_path: str) -> str:
    """Ссылка для Instagram: через Google Cloud Storage, при ошибке — через jsDelivr."""
    try:
        return storage.public_url(ROOT / rel_path, rel_path)
    except Exception as e:
        print(f"Cloud Storage недоступен, беру jsDelivr: {e}")
        return media_url(rel_path)


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
                "carousel": "Карусель", "reel_text": "Текстовый Reels",
                "reel_chat": "Reels-переписка", "reel_kinetic": "Reels «Кинетический текст»",
                "reel_story": "Reels «Кино»"}
FMT_SHORT = {"reel_chat": "ролик-переписка", "reel_kinetic": "ролик с цифрами", "carousel": "карусель",
             "reel_story": "кино-ролик"}
FRAME_LAYOUT = ("characters in the lower two thirds of the frame with full bodies visible, both clearly visible "
                "and not overlapping; the top quarter of the frame is a plain empty wall (space for captions)")


# ---------- сборка поста ----------

def build_post(item: dict, feedback: str = "") -> dict:
    fmt = item.get("format", "reel_scene")
    if item.get("script") and not feedback:  # готовый сценарий (например, от Марины из SYNTX)
        script = json.loads((ROOT / item["script"]).read_text(encoding="utf-8"))
    else:
        script = text.write_post(item, feedback)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    base = f"posts/{item['id']}/{stamp}"
    (POSTS / item["id"]).mkdir(parents=True, exist_ok=True)
    if not script.get("hook") and script.get("dialog"):
        script["hook"] = " — ".join(m["text"] for m in script["dialog"]).replace("*", "")
    job = {"id": item["id"], "format": fmt, "hook": script["hook"], "caption": script["caption"],
           "script": script, "warnings": []}
    job["base"] = base

    if fmt == "carousel":
        job["hook"] = job["hook"].replace("*", "")
        stories.canvas(True)  # слайды 4:5 на журнальном движке, как «Актуальное»
        try:
            kicker = script.get("kicker") or item.get("rubric", "")
            cards = script["cards"]
            n = len(cards) + 2
            cover = stories.slide({"type": "hero", "title": script["hook"], "sub": script.get("cover_sub", "")},
                                  kicker, f"1/{n}", _checked_or_none(job, script["cover_scene"], "4:5"))
            cover = render.swipe_badge(cover)  # без призыва листать мамы не понимают, что это карусель
            blobs = [cover] + [stories.slide(c, kicker, f"{i}/{n}") for i, c in enumerate(cards, 2)]
            fin = script["final"]
            blobs.append(stories.slide({"type": "cta", "title": fin.get("title", ""), "sub": fin.get("sub", ""),
                                        "link": item.get("cta") == "Ссылка в профиле"}, kicker, f"{n}/{n}"))
        finally:
            stories.canvas(False)
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

    if fmt == "reel_story":
        job["hook"] = job["hook"].replace("*", "")
        rel = f"{base}_reel.mp4"
        music = (os.environ.get("MUSIC") or "on").lower() != "off"
        veo = (os.environ.get("CINE_VEO") or "on").lower() == "on"  # без движения кино-ролик превращается в слайд-шоу
        res = cinematic.render_reel(script, ROOT / rel, with_music=music, veo=veo)
        job["video"] = rel
        job["warnings"] = (job.get("warnings") or []) + res["warnings"]
        job["cover"] = f"{base}_cover.jpg"
        (ROOT / job["cover"]).write_bytes(res["cover"])
        job["pics"] = []
        for i, pic in enumerate(res["pictures"], 1):
            prel = f"{base}_k{i}.jpg"
            (ROOT / prel).write_bytes(pic)
            job["pics"].append(prel)
        job["stage"] = "ready"
        save_job(job)
        return job

    if fmt == "reel_kinetic":
        job["hook"] = job["hook"].replace("*", "")
        photo, rep = images.life_checked(script["first_scene"], "9:16")
        if photo is None:
            job["warnings"].append("Кадр из жизни не прошёл проверку на брак — первый слайд без фото.")
        else:
            photo = _to_jpg(photo)
        rel = f"{base}_reel.mp4"
        music = (os.environ.get("MUSIC") or "on").lower() != "off"
        res = kinetic.render(script, photo, script.get("kicker") or item.get("rubric", ""), ROOT / rel, with_music=music)
        job["video"] = rel
        job["warnings"] = (job.get("warnings") or []) + res["warnings"]
        job["cover"] = f"{base}_cover.jpg"
        (ROOT / job["cover"]).write_bytes(res["cover"])
        job["pics"] = []
        for i, pic in enumerate(res["pictures"][1:], 1):
            prel = f"{base}_k{i}.jpg"
            (ROOT / prel).write_bytes(pic)
            job["pics"].append(prel)
        job["stage"] = "ready"
        save_job(job)
        return job

    if fmt == "reel_chat":
        job["dialog"] = script["dialog"]
        job["slides"] = []
        # Один проверенный кадр на весь ролик: каждый лишний кадр нейросети — новый шанс на брак (три руки и т.п.)
        photo, rep = images.life_checked(script["first_scene"], "9:16")
        if photo is None:  # люди не получились без брака — та же обстановка без людей
            problems = "; ".join((rep or {}).get("problems", [])[:3])
            photo, rep2 = images.life_checked(images.without_people(script["first_scene"]), "9:16", tries=2)
            job["warnings"].append(f"Кадр с людьми не прошёл проверку на брак ({problems}) — "
                                   + ("взяла кадр той же обстановки без людей." if photo else "ролик на фоне мессенджера."))
            photo = photo or render._chat_wallpaper()
        first_rel = f"{base}_slide1.jpg"
        (ROOT / first_rel).write_bytes(_to_jpg(photo))
        rest_rel = f"{base}_slide2.jpg"
        (ROOT / rest_rel).write_bytes(_soft_bg(photo))
        first = {"text": "", "scene": script["first_scene"], "motion": script.get("first_motion", "")}
        for i, sl in enumerate([first] + script["slides"]):
            job["slides"].append({**sl, "image": first_rel if i == 0 else rest_rel})
        cover = f"{base}_cover.jpg"
        # обложка: переписка ниже верхнего края — в сетке профиля Instagram показывает середину 3:4 (y 240–1680)
        (ROOT / cover).write_bytes(render.chat_cover((ROOT / job["slides"][0]["image"]).read_bytes(), job["dialog"], top=400))
        job["cover"] = cover
        assemble(job)
        return job

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


MIN_SCORE = int(os.environ.get("MIN_SCORE") or 9)
MAX_TRIES = int(os.environ.get("CRITIC_TRIES") or 3)


def _pictures(job) -> list:
    """Что увидит мама: обложка и кадры с надписями."""
    pics = []
    if job.get("cover"):
        pics.append((ROOT / job["cover"]).read_bytes())
    if job["format"] == "carousel":
        pics += [(ROOT / f).read_bytes() for f in job["files"][:4]]
    elif job.get("pics"):
        pics += [(ROOT / f).read_bytes() for f in job["pics"][:5]]
    elif job.get("slides"):
        for sl in job["slides"][1:5]:
            msg = {"who": "brand", "text": sl["text"]} if job["format"] == "reel_chat" else None
            img = (ROOT / sl["image"]).read_bytes()
            pics.append(render.chat_cover(img, [msg]) if msg else img)
    elif job.get("shots"):
        pics += [(ROOT / s["frame"]).read_bytes() for s in job["shots"]]
    return pics


def animate_chat(job) -> None:
    """Оживляет одобренные кадры Reels-переписки через Veo; не получилось — кадр остаётся картинкой."""
    if (os.environ.get("CHAT_VIDEO") or "off").lower() != "veo":
        return  # по умолчанию без Veo: оживление людей даёт брак (лишние руки, «плывущие» лица)
    failed = 0
    for i, sl in enumerate(job["slides"]):
        if sl.get("clip"):
            continue
        motion = sl.get("motion") or "Subtle natural movement, the woman smiles."
        src = (ROOT / sl["image"]).read_bytes()
        secs = 6 if i == 0 else 4
        for attempt in range(2):  # склеенный или «уехавший» клип переделываем один раз, потом оставляем картинку
            try:
                data = video.veo_clip(src, motion, secs, style=video.LIFE_VIDEO)
                problem = video.clip_problem(data, src, secs)
                if problem:
                    print(f"Veo, кадр {i + 1}, попытка {attempt + 1}: брак — {problem}")
                    continue
                rel = f"{job['base']}_clip{i + 1}.mp4"
                (ROOT / rel).write_bytes(data)
                sl["clip"] = rel
                break
            except Exception as e:
                print(f"Veo, кадр {i + 1}: {e}")
                break
        if not sl.get("clip"):
            failed += 1
    assemble(job)
    if failed:
        job["warnings"] = (job.get("warnings") or []) + [f"Veo не оживил {failed} кадр(а) — они остались картинками."]
        save_job(job)


def _drop_files(job):
    """Удаляет картинки и видео отвергнутого варианта, чтобы не раздувать репозиторий."""
    folder = POSTS / job["id"]
    stamp = (job.get("base") or "").rsplit("/", 1)[-1]
    if not stamp:
        return
    for f in folder.glob(f"{stamp}_*"):
        f.unlink()


def build_checked(item: dict, feedback: str = "") -> dict:
    """Собирает пост и прогоняет через строгого критика; ниже MIN_SCORE — переделывает (до MAX_TRIES раз)."""
    best = None
    notes = feedback
    # кино-ролик дорогой (десятки кадров), а готовый сценарий не переписываем — один заход, критик только оценивает
    tries = 1 if item.get("format") == "reel_story" or item.get("script") else MAX_TRIES
    for attempt in range(tries):
        job = build_post(item, notes)
        try:
            review = text.critique(job["script"], _pictures(job))
        except Exception as e:
            job["review"] = {"score": 0, "verdict": f"критик не ответил: {e}"[:300], "fixes": []}
            save_job(job)
            return job
        job["review"] = review
        job["attempt"] = attempt + 1
        save_job(job)
        print(f"Критик: {review['score']}/10 — {review.get('verdict')}")
        if not best or review["score"] > best["review"]["score"]:
            if best:
                _drop_files(best)
            best = job
        else:
            _drop_files(job)
        if review["score"] >= MIN_SCORE:
            if job["format"] == "reel_chat":
                animate_chat(job)
            return job
        fixes = "; ".join(review.get("fixes") or [])
        notes = (feedback + "\n" if feedback else "") + f"Строгий редактор поставил {review['score']}/10: {review.get('verdict')} Исправь: {fixes}"
    if best is not job:  # вернуть лучший вариант
        save_job(best)
    if best["format"] == "reel_chat":
        animate_chat(best)
    return best


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
    if job["format"] == "reel_chat":
        slides = [{"image": (ROOT / sl["image"]).read_bytes(), "text": sl["text"],
                   "clip": (ROOT / sl["clip"]) if sl.get("clip") else None} for sl in job["slides"]]
        slides[0]["dialog"] = job["dialog"]
        rel = f"{job['base']}_reel.mp4"
        job["warnings"] = [w for w in (job.get("warnings") or []) if "Veo" not in w and "музык" not in w] + \
            video.assemble_chat(slides, job.get("end", ""), ROOT / rel, with_music=music)
        job["video"] = rel
        job["stage"] = "ready"
        save_job(job)
        return
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
        thumb = (ROOT / job["cover"]).read_bytes() if job.get("cover") and (ROOT / job["cover"]).exists() else None
        try:
            secs = int(video.duration(ROOT / job["video"]))
        except Exception:  # noqa: BLE001
            secs = None
        telegram.send_video(cid, (ROOT / job["video"]).read_bytes(), title, thumb=thumb, duration=secs)
    telegram.send_text(cid, job["caption"][:3900])
    rv = job.get("review")
    if rv:
        tries = f" (вариант {job.get('attempt', 1)} из {MAX_TRIES})" if job.get("attempt", 1) > 1 else ""
        msg = f"🧐 Строгий критик: {rv.get('score')}/10{tries}. {rv.get('verdict', '')}"
        if rv.get("fixes"):
            msg += "\n\nЧто можно улучшить:\n" + "\n".join(f"• {f}" for f in rv["fixes"][:4])
        telegram.send_text(cid, msg[:3900])
    for w in job.get("warnings") or []:
        telegram.send_text(cid, f"ℹ️ {w}"[:3900])
    buttons = [[
        {"text": "✅ Публиковать", "callback_data": f"pub:{pid}"},
        {"text": "🔁 Переделать", "callback_data": f"redo:{pid}"},
    ]]
    if rv and rv.get("fixes"):
        buttons.append([{"text": "🛠 Внести правки критика", "callback_data": f"fix:{pid}"}])
    buttons.append([{"text": "⏭ Пропустить", "callback_data": f"skip:{pid}"}])
    telegram.send_text(cid, "Публикуем?", buttons)


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
        link = instagram.publish_carousel([public_url(f) for f in job["files"]], job["caption"])
    else:
        cover = public_url(job["cover"]) if job.get("cover") else ""
        link = instagram.publish_reel(public_url(job["video"]), job["caption"], cover)
    if vposter.enabled():
        try:
            if job["format"] == "carousel":
                where = vposter.publish(job["caption"], images=[ROOT / f for f in job["files"]])
            else:
                where = vposter.publish(job["caption"], video=ROOT / job["video"])
            state.setdefault("vk_sent", []).append(link.strip())
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


def top_up_plan(state, force=False):
    """Когда впереди меньше 4 постов — дописывает неделю по гиду и присылает список тем."""
    if not force and planner.remaining(state["done"], state.get("pending")) >= 4:
        return
    try:
        new = planner.extend()
    except Exception as e:  # noqa: BLE001
        telegram.send_text(chat(state), f"⚠️ Не получилось дописать контент-план: {e}"[:3900])
        return
    git_sync("План: дописана неделя по гиду")
    lines = [f"{p['date'][8:10]}.{p['date'][5:7]} · {p.get('type', '')} · {FMT_SHORT.get(p['format'], 'ролик')} — {p['hook']}"
             for p in new]
    telegram.send_text(chat(state), "🗓 Дописала план на неделю по гиду:\n" + "\n".join(lines)
                       + "\n\nЧто-то не нравится — напишите мне в Claude, поправлю.")


def _prepare(state):
    if not chat(state):
        print("Чат с ботом ещё не найден: напишите боту в Telegram любое сообщение.")
        return
    if state.get("pending"):
        telegram.send_text(chat(state), "Напоминаю: предыдущий пост ждёт решения 👆")
        return
    top_up_plan(state)
    item = next_post(state)
    if not item:
        telegram.send_text(chat(state), "Контент-план закончился, а дописать его не получилось. Напишите /plan — попробую ещё раз.")
        return
    job = build_checked(item)
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


def _checked_or_none(job, scene, aspect):
    photo, rep = images.life_checked(scene, aspect)
    if photo is None:
        job["warnings"].append("Кадр для обложки не прошёл проверку на брак — обложка без фото.")
    return photo


def _soft_bg(data: bytes) -> bytes:
    """Фон для сообщений после диалога: тот же кадр, размытый и чуть светлее — внимание на тексте."""
    import io
    from PIL import Image, ImageEnhance, ImageFilter
    img = render.fit_vertical(data).filter(ImageFilter.GaussianBlur(22))
    img = ImageEnhance.Brightness(img).enhance(1.08)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=90)
    return out.getvalue()


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
    all_h = yaml.safe_load(HIGHLIGHTS.read_text(encoding="utf-8"))["highlights"]
    want = only.replace(",", " ").split()
    data = [h for w in want for h in all_h if h["id"] == w] if want else all_h  # в том порядке, как просили
    if only and not data:
        if cid:
            telegram.send_text(cid, "Нет такого хайлайта. Есть: " + ", ".join(h["id"] for h in all_h))
        return
    for h in data:
        files, auto, notes = [], [], []
        me = storage.get_private("private/me.jpg") if h["id"] == "kto-ya" else None
        if h["id"] == "kto-ya" and not me and cid:
            telegram.send_text(cid, "Для «Кто я» нужно ваше фото: пришлите его сюда с подписью «кто я». "
                                    "Пока делаю без фото.")
        cover = render.highlight_cover_photo(me) if me else render.highlight_cover(h.get("icon", ""))
        n = len(h["stories"])
        for i, s in enumerate(h["stories"], 1):
            img = me if s.get("photo") == "me" else None
            if s.get("photo") == "me" and not me:
                s = {**s, "type": "qa", "q": s["title"], "a": s.get("sub", "")}
            if s.get("scene") and img is None:
                try:
                    img = images.life(s["scene"], "9:16")
                except Exception as e:  # noqa: BLE001
                    notes.append(f"Сторис {i}: кадр не нарисовался ({str(e)[:80]}), сделала без него")
            files.append(stories.slide(s, h["title"], f"{i}/{n}", img))
            auto.append(stories.slide(s, h["title"], f"{i}/{n}", img, auto=True))
            if s.get("sticker"):
                notes.append(f"Сторис {i}: добавьте стикер — {s['sticker']}")
        if not cid:
            continue
        for start in range(0, len(files), 10):
            telegram.send_album(cid, files[start:start + 10], f"«{h['title']}»: {len(files)} сторис по порядку")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr(f"{h['id']}-00-oblozhka.jpg", cover)
            for i, pic in enumerate(files, 1):
                z.writestr(f"{h['id']}-{i:02d}.jpg", pic)
        telegram.send_document(cid, buf.getvalue(), f"{h['id']}.zip",
                               "Файлы в полном качестве: 00 — обложка, дальше сторис по порядку")
        # версия для публикации ботом: заливаем в облако, ссылки живут сутки
        urls = []
        tmp = POSTS / "_stories"
        tmp.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d%H%M%S")
        for i, pic in enumerate(auto, 1):
            f = tmp / f"{h['id']}-{i:02d}.jpg"
            f.write_bytes(pic)
            urls.append(storage.public_url(f, f"stories/{h['id']}-{stamp}-{i:02d}.jpg"))
            f.unlink()
        state.setdefault("stories_ready", {})[h["id"]] = {"urls": urls, "at": time.time()}
        save_state(state)
        tips = "\n".join(notes) if notes else "Стикеры не нужны."
        telegram.send_text(cid, f"«{h['title']}» — как выложить:\n\n"
                                f"⚡ Быстро: кнопка ниже — бот сам опубликует {len(urls)} сторис по порядку. "
                                f"Стикеров не будет (их нельзя поставить через Instagram API): опросы пропадут, "
                                f"а на последней сторис вместо стикера-ссылки будет «samreshu.pro — ссылка в шапке профиля».\n\n"
                                f"✋ Вручную со стикерами: выложите файлы из архива и добавьте:\n{tips}\n\n"
                                f"Потом в профиле: «Актуальное» → «+» → отметьте эти сторис → название «{h['title']}» → "
                                f"обложка — файл 00 из архива.\nНе нравится? /stories {h['id']} — нарисую заново.",
                           [[{"text": f"📤 Выложить «{h['title']}» в сторис", "callback_data": f"story:{h['id']}"}]])


# ---------- проба визуала ----------

PROBE = [
    ("Мама ночью в родительском чате",
     "Late evening in a modern renovated apartment kitchen. A woman about 42 sits at the table under a warm lamp, "
     "looking at her phone with a tired, worried face; a cup of tea gone cold, school notebooks on the table."),
    ("Закрытая дверь и «не знаю»",
     "View from a dim hallway into a teenager's room through a half-open door: a teenager in an oversized sage hoodie "
     "and baggy jeans sits cross-legged on an unmade bed in a modern teen room wearing big wireless headphones, back to the camera, face not visible, lit by a phone screen; "
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
        if kind in ("pub", "redo", "again", "skip", "veo", "still", "fix") and state.get("pending") != arg:
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
        elif kind == "fix" and state.get("pending") == arg:
            fix_by_critic(state, arg)
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
        elif kind == "story":
            ready = (state.get("stories_ready") or {}).get(arg)
            if not ready or time.time() - ready["at"] > 20 * 3600:
                telegram.send_text(cid, f"Эти сторис устарели. Отправьте /stories {arg} — нарисую и пришлю заново.")
            else:
                telegram.send_text(cid, f"Публикую {len(ready['urls'])} сторис по порядку…")
                done = 0
                for url in ready["urls"]:
                    try:
                        instagram.publish_story(url)
                        done += 1
                        time.sleep(5)
                    except Exception as e:  # noqa: BLE001
                        telegram.send_text(cid, f"⚠️ Сторис {done + 1} не вышла: {e}"[:1500])
                        break
                state["stories_ready"].pop(arg, None)
                telegram.send_text(cid, f"✅ Опубликовано сторис: {done}.\nТеперь в профиле: «Актуальное» → «+» → "
                                        "отметьте их → название → обложка из архива (файл 00).")
        elif kind == "vkall":
            vk_import(state, confirm=True)
        elif kind == "refs":
            telegram.send_text(cid, "Рисую новые варианты, это займёт пару минут…")
            cmd_refs(state)
        return

    doc = msg.get("document") or {}
    pic = (msg.get("photo") or [None])[-1] or (doc if str(doc.get("mime_type", "")).startswith("image/") else None)
    if pic and "кто я" in (msg.get("caption") or "").lower():
        try:
            storage.put_private(telegram.download(pic["file_id"]), "private/me.jpg")
            telegram.send_text(cid, "📸 Фото сохранено для «Кто я». Теперь отправьте /stories kto-ya — "
                                    "фото будет на первой сторис и на обложке.")
        except Exception as e:  # noqa: BLE001
            telegram.send_text(cid, f"⚠️ Не получилось сохранить фото: {e}"[:1500])
        return
    audio = msg.get("audio") or (msg.get("document") if str((msg.get("document") or {}).get("mime_type", "")).startswith("audio/") else None)
    if audio:
        lib = ROOT / "music"
        lib.mkdir(exist_ok=True)
        name = audio.get("file_name") or f"{audio.get('performer', 'track')}-{audio.get('title', audio['file_unique_id'])}.mp3"
        name = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
        (lib / name).write_bytes(telegram.download(audio["file_id"]))
        n = len([p for p in lib.iterdir() if p.is_file()])
        git_sync(f"Музыка: {name}")
        telegram.send_text(cid, f"🎵 Трек добавлен в музыку роликов. Сейчас в библиотеке: {n}. "
                                "Ролики берут треки отсюда по очереди. Убрать трек — /music.")
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
    elif body.startswith("/profile"):
        try:
            pr = instagram.profile()
            media = instagram.my_media()
            bio = pr.get("biography") or "— пусто —"
            telegram.send_text(cid, "Профиль сейчас:\n"
                               f"Ник: @{pr.get('username')}\nИмя: {pr.get('name') or '— пусто —'}\n"
                               f"Шапка ({len(pr.get('biography') or '')} знаков):\n{bio}\n"
                               f"Ссылка: {pr.get('website') or '— нет —'}\n"
                               f"Публикаций: {pr.get('media_count')}, подписчиков: {pr.get('followers_count')}\n\n"
                               "Последние посты:\n" + "\n".join(
                                   f"{m['date']} · {m['type']} · {(m['caption'] or '').splitlines()[0][:50] if m['caption'] else ''}"
                                   for m in media[-12:]))
            if pr.get("profile_picture_url"):
                telegram.send_photo(cid, requests.get(pr["profile_picture_url"], timeout=60).content, "Аватар сейчас")
        except Exception as e:  # noqa: BLE001
            telegram.send_text(cid, f"⚠️ Не получилось прочитать профиль: {e}"[:1500])
    elif body.startswith("/vk_test"):
        try:
            m = next(m for m in reversed(instagram.my_media()) if m["type"] == "VIDEO")
            f = ROOT / "posts" / "_vk_test.mp4"
            f.write_bytes(requests.get(m["files"][0][1], timeout=300).content)
            gid = vposter.targets()[0][1]
            try:
                att = vposter._upload(f, gid, "video")
                msg = json.dumps(att, ensure_ascii=False)
            except Exception as e:  # noqa: BLE001
                msg = f"ошибка: {e}"
            telegram.send_text(cid, f"Тест загрузки видео (без публикации). Файл {f.stat().st_size // 1024} КБ.\n"
                                    f"Ответ Впостера: {msg}"[:3900])
            f.unlink()
        except Exception as e:  # noqa: BLE001
            telegram.send_text(cid, f"⚠️ Тест не удался: {e}"[:3900])
    elif body.startswith("/vk_all"):
        vk_import(state, confirm=False)
    elif body.startswith("/vk"):
        if not vposter.enabled():
            telegram.send_text(cid, "Ключа Впостера пока нет: в GitHub не найден секрет VPOSTER_TOKEN.")
        else:
            try:
                pages = vposter.targets()
                telegram.send_text(cid, "✅ Впостер подключён. Посты будут уходить сюда:\n"
                                   + "\n".join(f"• {vposter.NAMES.get(s_, s_)}: {n} (id {g})" for s_, g, n in pages))
            except Exception as e:  # noqa: BLE001
                telegram.send_text(cid, f"⚠️ Впостер не отвечает как надо: {e}"[:3900])
    elif body.startswith("/plan"):
        ahead = [p for p in load_plan() if p["id"] not in state["done"]]
        if len(ahead) < 4:
            top_up_plan(state, force=True)
            ahead = [p for p in load_plan() if p["id"] not in state["done"]]
        telegram.send_text(cid, "Ближайшие посты:\n" + "\n".join(
            f"{str(p['date'])[8:10]}.{str(p['date'])[5:7]} · {FMT_SHORT.get(p['format'], 'ролик')} — {p['hook']}"
            for p in ahead[:10]))
    elif body.startswith("/fix"):
        if state.get("pending") and (POSTS / state["pending"] / "post.json").exists():
            fix_by_critic(state, state["pending"])
        else:
            telegram.send_text(cid, "Сейчас нечего править.")
    elif body.startswith("/rebuild"):
        pid = state.get("pending")
        if not pid or not (POSTS / pid / "post.json").exists() or load_job(pid)["format"] in ("carousel", "reel_story", "reel_kinetic"):
            telegram.send_text(cid, "Сейчас нечего пересобирать.")
        else:
            job = load_job(pid)
            telegram.send_text(cid, "Пересобираю ролик из готовых кадров и клипов, пара минут…")
            job["cover"] = f"{job['base']}_cover.jpg"
            first = (ROOT / (job["shots"][0]["frame"] if job.get("shots") else job["slides"][0]["image"])).read_bytes()
            (ROOT / job["cover"]).write_bytes(render.chat_cover(first, job["dialog"], top=400) if job.get("dialog")
                                              else render.reel_cover(first, job["hook"]))
            assemble(job)
            send_draft(state, job)
    elif body.startswith("/music"):
        lib = ROOT / "music"
        files = sorted(p for p in lib.glob("*.*")) if lib.exists() else []
        arg = body[len("/music"):].strip()
        if arg.isdigit() and 1 <= int(arg) <= len(files):
            files[int(arg) - 1].unlink()
            git_sync("Музыка: трек удалён")
            telegram.send_text(cid, "Трек удалён.")
        elif files:
            telegram.send_text(cid, "Музыка роликов:\n" + "\n".join(f"{i}. {p.name}" for i, p in enumerate(files, 1))
                               + "\n\nУдалить: /music 2")
        else:
            telegram.send_text(cid, "Своих треков пока нет — музыку пишет нейросеть. Пришлите сюда mp3, и ролики будут брать его.")
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


def fix_by_critic(state, ep_id):
    rv = load_job(ep_id).get("review") or {}
    fixes = rv.get("fixes") or []
    if not fixes:
        telegram.send_text(chat(state), "У критика нет замечаний к этому посту.")
        return
    feedback = ("Сохрани всё, что получилось хорошо: тему, сюжет, структуру и удачные формулировки. "
                "Внеси только эти правки строгого критика:\n" + "\n".join(f"- {f}" for f in fixes))
    regenerate(state, ep_id, feedback)


def vk_import(state, confirm):
    """Переносит во ВКонтакте посты, которые уже вышли в Instagram."""
    cid = chat(state)
    if not vposter.enabled():
        telegram.send_text(cid, "Впостер не подключён.")
        return
    sent = set(state.setdefault("vk_sent", []))
    until = state.get("vk_synced_until", "")  # всё, что вышло до этой даты включительно, уже во ВКонтакте
    todo = [m for m in instagram.my_media() if m["id"] not in sent and m["link"] not in sent and m["files"]
            and m["date"] > until]
    if not todo:
        telegram.send_text(cid, "Все посты из Instagram уже есть во ВКонтакте.")
        return
    if not confirm:
        lines = [f"{m['date'][8:10]}.{m['date'][5:7]} · {'ролик' if m['type'] == 'VIDEO' else 'карусель' if m['type'] == 'CAROUSEL_ALBUM' else 'фото'} — "
                 f"{(m['caption'] or '').splitlines()[0][:60] if m['caption'] else 'без подписи'}" for m in todo]
        telegram.send_text(cid, f"Во ВКонтакте можно перенести {len(todo)}:\n" + "\n".join(lines),
                           [[{"text": "📤 Отправить во ВКонтакте", "callback_data": "vkall:go"}]])
        return
    telegram.send_text(cid, f"Переношу {len(todo)} во ВКонтакте, по минуте на пост…")
    tmp = ROOT / "posts" / "_vk_import"
    tmp.mkdir(parents=True, exist_ok=True)
    for m in todo:
        try:
            paths = []
            for i, (kind, url) in enumerate(m["files"]):
                f = tmp / f"{m['id']}_{i}.{'mp4' if kind == 'VIDEO' else 'jpg'}"
                f.write_bytes(requests.get(url, timeout=300).content)
                paths.append(f)
            videos = [p for p in paths if p.suffix == ".mp4"]
            if videos and len(paths) == 1:
                vposter.publish(m["caption"], video=videos[0])
            else:
                vposter.publish(m["caption"], images=[p for p in paths if p.suffix == ".jpg"])
            state["vk_sent"].append(m["link"] or m["id"])
            save_state(state)
            telegram.send_text(cid, f"✅ {m['date'][8:10]}.{m['date'][5:7]} отправлен во ВКонтакте")
        except Exception as e:  # noqa: BLE001
            telegram.send_text(cid, f"⚠️ Пост {m['link']} не получилось: {e}"[:1500])
        finally:
            for f in tmp.glob(f"{m['id']}_*"):
                f.unlink()
        time.sleep(60)
    git_sync("ВКонтакте: перенесены посты из Instagram")


def regenerate(state, ep_id, feedback):
    telegram.send_text(chat(state), "Переделываю и показываю строгому критику, это займёт 5–15 минут…")
    try:
        old = load_job(ep_id)
    except Exception:  # noqa: BLE001
        old = None
    job = build_checked(post_by_id(ep_id), feedback)
    if old and old.get("base") and old.get("base") != job.get("base"):
        _drop_files(old)  # файлы прошлого варианта больше не нужны — не раздуваем репозиторий
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


def cmd_voices(state):
    """Пробник голосов мамы от Яндекса: присылает в Telegram одну и ту же фразу разными голосами."""
    from . import yandex_tts
    if not chat(state):
        raise RuntimeError("Не знаю чат: напишите боту любое сообщение и запустите снова")
    if not yandex_tts.enabled():
        telegram.send_text(chat(state), "Нет ключа Яндекса: добавьте секрет YANDEX_TTS_KEY в настройках репозитория.")
        return
    phrase = os.environ.get("VOICE_SAMPLE_TEXT") or (
        "Ну что, какие предметы ОГЭ берёшь? Это не глупость. С друзьями подростки решают смелее — так устроен возраст.")
    telegram.send_text(chat(state), "🎙 Пробник голосов мамы от Яндекса. Послушайте и напишите сюда или Claude номер, "
                                    "который звучит живее всего.")
    errors = []
    for n, (model, voice, role) in enumerate(yandex_tts.SAMPLES, 1):
        try:
            telegram.send_voice(chat(state), yandex_tts.sample_ogg(phrase, model, voice, role),
                                caption=f"{n}. {voice} · {role}" + (" · livetts" if model != "general" else ""))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{n}. {voice}/{role}: {str(e)[:120]}")
    if errors:
        print("\n".join(errors))
        (ROOT / "voices_report.txt").write_text("\n".join(errors), encoding="utf-8")
        telegram.send_text(chat(state), "Не получились:\n" + "\n".join(errors))
    else:
        (ROOT / "voices_report.txt").write_text("ok", encoding="utf-8")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "poll"
    state = load_state()
    try:
        {"prepare": cmd_prepare, "poll": cmd_poll, "refs": cmd_refs, "refresh": cmd_refresh,
         "stories": cmd_stories, "voices": cmd_voices}[cmd](state)
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
