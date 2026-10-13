"""Автопилот «Сам решу».

python -m autopilot.main prepare   — подготовить пост дня и прислать в Telegram на проверку
python -m autopilot.main poll      — обработать кнопки и сообщения из Telegram (и опубликовать)
python -m autopilot.main refs      — сгенерировать варианты эталонов Сама и Надо
python -m autopilot.main refresh   — продлить маркер Instagram
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

from . import images, instagram, render, telegram, text
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
        return json.loads(STATE.read_text(encoding="utf-8"))
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


def raw_url(rel_path: str) -> str:
    repo = os.environ["GITHUB_REPOSITORY"]
    branch = os.environ.get("GITHUB_REF_NAME") or "main"
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel_path}"


def chat(state):
    return os.environ.get("TG_CHAT_ID") or state.get("chat_id")


# ---------- посты ----------

def next_episode(state):
    plan = yaml.safe_load((ROOT / "plan.yaml").read_text(encoding="utf-8"))["episodes"]
    for ep in plan:
        if ep["id"] not in state["done"]:
            return ep
    return None


def episode_by_id(ep_id):
    plan = yaml.safe_load((ROOT / "plan.yaml").read_text(encoding="utf-8"))["episodes"]
    return next(ep for ep in plan if ep["id"] == ep_id)


def build_post(ep: dict, feedback: str = "") -> dict:
    script = text.write_episode(ep["idea"], ep.get("audience", "school"), feedback)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    folder = POSTS / ep["id"]
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for i, p in enumerate(script["panels"], 1):
        raw = images.panel(p["scene"])
        jpg = render.compose(raw, p["speaker"], p["line"], f"{i}/4")
        rel = f"posts/{ep['id']}/{stamp}_{i}.jpg"
        (ROOT / rel).write_bytes(jpg)
        files.append(rel)
    post = {"id": ep["id"], "files": files, "caption": script["caption"], "panels": script["panels"]}
    (folder / "post.json").write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")
    return post


def send_draft(state, post):
    cid = chat(state)
    telegram.send_album(cid, [(ROOT / f).read_bytes() for f in post["files"]])
    telegram.send_text(cid, post["caption"][:3900])
    telegram.send_text(cid, "Публикуем?", [[
        {"text": "✅ Публиковать", "callback_data": f"pub:{post['id']}"},
        {"text": "🔁 Переделать", "callback_data": f"redo:{post['id']}"},
    ], [{"text": "⏭ Пропустить эпизод", "callback_data": f"skip:{post['id']}"}]])


def publish(state, ep_id):
    post = json.loads((POSTS / ep_id / "post.json").read_text(encoding="utf-8"))
    git_sync(f"Картинки поста {ep_id}")
    time.sleep(20)  # даём GitHub время раздать файлы по ссылке
    link = instagram.publish_carousel([raw_url(f) for f in post["files"]], post["caption"])
    state["done"].append(ep_id)
    state["pending"] = None
    state["awaiting_feedback"] = None
    return link


def cmd_prepare(state):
    if not chat(state):
        print("Чат с ботом ещё не найден: напишите боту в Telegram любое сообщение.")
        return
    if state.get("pending"):
        telegram.send_text(chat(state), f"Напоминаю: пост «{state['pending']}» ждёт решения 👆")
        return
    ep = next_episode(state)
    if not ep:
        telegram.send_text(chat(state), "Контент-план закончился. Допишите эпизоды в plan.yaml.")
        return
    post = build_post(ep)
    state["pending"] = ep["id"]
    if os.environ.get("AUTO_PUBLISH", "").lower() == "true":
        link = publish(state, ep["id"])
        telegram.send_text(chat(state), f"Опубликовано: {link}")
    else:
        send_draft(state, post)


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


# ---------- Telegram ----------

def cmd_poll(state):
    if not chat(state):
        me = telegram._api("getMe")
        hook = telegram._api("getWebhookInfo")
        print(f"Бот: @{me.get('username')} ({me.get('first_name')})")
        if hook.get("url"):
            print("У бота был включён вебхук — отключаю, иначе сообщения не доходят.")
            telegram._api("deleteWebhook", json={"drop_pending_updates": False})
    ups = telegram.updates(state["offset"])
    print(f"Новых сообщений и нажатий: {len(ups)}")
    for up in ups:
        state["offset"] = up["update_id"] + 1
        try:
            handle(state, up)
        except Exception as e:  # сообщаем об ошибке и идём дальше
            if chat(state):
                telegram.send_text(chat(state), f"⚠️ Ошибка: {e}"[:3900])
            print("Ошибка:", e, file=sys.stderr)
        save_state(state)


def handle(state, up):
    msg = up.get("message")
    cb = up.get("callback_query")
    src = (msg or {}).get("chat") or ((cb or {}).get("message") or {}).get("chat") or {}
    if not state.get("chat_id") and src.get("type") == "private":
        state["chat_id"] = src["id"]
        telegram.send_text(src["id"], "Привет! Я автопилот «Сам решу». Буду присылать сюда посты на проверку.\n"
                                      "Команды: /post — сделать пост сейчас, /refs — нарисовать эталоны персонажей.")
        return
    if str(src.get("id")) != str(chat(state)):
        return  # чужие чаты игнорируем
    cid = chat(state)

    if cb:
        telegram.answer(cb["id"])
        kind, _, arg = cb["data"].partition(":")
        if kind == "pub" and state.get("pending") == arg:
            telegram.send_text(cid, "Публикую… ⏳")
            telegram.send_text(cid, f"Готово! {publish(state, arg)}")
        elif kind == "redo" and state.get("pending") == arg:
            state["awaiting_feedback"] = arg
            telegram.send_text(cid, "Напишите одним сообщением, что поправить. Или нажмите кнопку — сделаю другой вариант.",
                               [[{"text": "🎲 Просто другой вариант", "callback_data": f"again:{arg}"}]])
        elif kind == "again" and state.get("pending") == arg:
            regenerate(state, arg, "")
        elif kind == "skip" and state.get("pending") == arg:
            state["done"].append(arg)
            state["pending"] = None
            state["awaiting_feedback"] = None
            telegram.send_text(cid, "Пропустили. Следующий эпизод — завтра (или /post прямо сейчас).")
        elif kind == "ref":
            key, _, num = arg.partition(":")
            shutil.copy(CANDIDATES / f"{key}_{num}.png", REFS / f"{key}.png")
            telegram.send_text(cid, f"Эталон «{NAMES[key]}» сохранён ✅")
        elif kind == "refs":
            telegram.send_text(cid, "Рисую новые варианты, это займёт пару минут…")
            cmd_refs(state)
        return

    body = (msg.get("text") or "").strip()
    if body.startswith("/post"):
        if state.get("pending"):
            telegram.send_text(cid, "Сначала решите судьбу предыдущего поста 👆")
        else:
            telegram.send_text(cid, "Готовлю пост, это займёт 2–4 минуты…")
            cmd_prepare(state)
    elif body.startswith("/refs"):
        telegram.send_text(cid, "Рисую эталоны персонажей, это займёт пару минут…")
        cmd_refs(state)
    elif state.get("awaiting_feedback") and body:
        regenerate(state, state["awaiting_feedback"], body)


def regenerate(state, ep_id, feedback):
    telegram.send_text(chat(state), "Переделываю, это займёт 2–4 минуты…")
    post = build_post(episode_by_id(ep_id), feedback)
    state["awaiting_feedback"] = None
    send_draft(state, post)


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
        {"prepare": cmd_prepare, "poll": cmd_poll, "refs": cmd_refs, "refresh": cmd_refresh}[cmd](state)
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
