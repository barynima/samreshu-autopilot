"""Публикация во ВКонтакте (и при желании в Telegram-канал, MAX, Одноклассники) через API сервиса Впостер.

Официальный API ВКонтакте даёт право публиковать на стене только по особому запросу в поддержку,
поэтому используем Впостер (vposter.ru): вход через VK ID, ключ API в разделе «Разработчикам».
Секрет VPOSTER_TOKEN — ключ API. Переменная VPOSTER_TARGETS — куда публиковать, по умолчанию «vk»
(можно «vk,tg,max,ok»). VPOSTER_VK_GROUP — ID сообщества, если в кабинете их несколько.
"""
import json
import os
import re
from pathlib import Path

import requests

API = "https://vposter.ru/api/v3/method"
NAMES = {"vk": "ВКонтакте", "tg": "Telegram", "max": "MAX", "ok": "Одноклассники"}


def enabled() -> bool:
    return bool(os.environ.get("VPOSTER_TOKEN"))


def _h():
    return {"Authorization": f"Bearer {os.environ['VPOSTER_TOKEN']}"}


def _check(r, what):
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Впостер {what}: HTTP {r.status_code} {r.text[:300]}")
    if not 200 <= r.status_code < 300 or "error" in data:
        raise RuntimeError(f"Впостер {what}: HTTP {r.status_code} {json.dumps(data, ensure_ascii=False)[:400]}")
    return data


def targets() -> list:
    """[(type_social, id_group, name)] — куда публикуем."""
    want = [t.strip() for t in (os.environ.get("VPOSTER_TARGETS") or "vk").split(",") if t.strip()]
    data = _check(requests.get(f"{API}/groups.getMy", headers=_h(), timeout=60), "groups.getMy")
    pages = {}

    def walk(node, social=None):
        if isinstance(node, dict):
            if "id" in node and ("name" in node or "type_social" in node):
                key = str(node.get("type_social") or social or "")
                key = {"vkontakte": "vk", "telegram": "tg", "odnoklassniki": "ok"}.get(key, key)
                pages.setdefault(key, []).append(node)
                return
            for k, v in node.items():
                walk(v, k if k in NAMES or k in ("fb", "ig_business", "rutube") else social)
        elif isinstance(node, list):
            for v in node:
                walk(v, social)

    walk(data.get("response", data))
    if not pages:
        raise RuntimeError("Впостер вернул неожиданный список страниц: "
                           + json.dumps(data, ensure_ascii=False)[:500])
    out = []
    for social in want:
        lst = pages.get(social) or []
        if not lst:
            raise RuntimeError(f"Впостер: нет подключённых страниц типа «{social}». Подключите сообщество в кабинете.")
        # Личные страницы никогда не трогаем: только сообщества.
        lst = [p for p in lst if "личная" not in str(p.get("name", "")).lower()
               and str(p.get("type", "")).lower() not in ("user", "profile")]
        if not lst:
            raise RuntimeError(f"Впостер: в «{NAMES.get(social, social)}» подключена только личная страница. "
                               "Подключите в кабинете Впостера сообщество «Сам решу».")
        pick = os.environ.get(f"VPOSTER_{social.upper()}_GROUP")
        if pick:
            page = next((p for p in lst if str(p["id"]).lstrip("-") == str(pick).lstrip("-")), None)
        else:
            named = [p for p in lst if "сам решу" in str(p.get("name", "")).lower()]
            page = named[0] if len(named) == 1 else (lst[0] if len(lst) == 1 else None)
        if not page:
            names = ", ".join(f"{p.get('name')} (id {p['id']})" for p in lst)
            raise RuntimeError(f"Впостер: не понятно, какое сообщество выбрать: {names}. "
                               f"Укажите нужный id в переменной VPOSTER_{social.upper()}_GROUP.")
        out.append((social, str(page["id"]), page.get("name", "")))
    return out


LAST_UPLOAD = {}


def _upload(path: Path, id_group: str, kind: str) -> list:
    mime = {"video": "video/mp4", "image": "image/jpeg"}.get(kind, "application/octet-stream")
    with open(path, "rb") as f:
        r = requests.post(f"{API}/uploads.uploadFile", headers=_h(), timeout=600,
                          files={"file": (path.name, f, mime)}, data={"id_group": id_group, "type": kind})
    data = _check(r, "uploadFile")
    LAST_UPLOAD.clear()
    LAST_UPLOAD.update(data)
    resp = data.get("response") or {}
    att = resp.get("attachments") or resp.get("attachements") or []  # у Впостера в ответе встречается опечатка
    if not att:
        raise RuntimeError(f"Впостер не вернул вложение для {path.name}: {json.dumps(data, ensure_ascii=False)[:400]}")
    return att


def adapt_caption(caption: str) -> str:
    """В Instagram ссылки в тексте не кликаются, во ВКонтакте — да."""
    text = re.sub(r"по ссылке в (шапке )?профил[еяю]", "по ссылке samreshu.pro", caption, flags=re.I)
    text = re.sub(r"(с)сылк([аеу]) в (шапке )?профил[еяю]", r"\1сылк\2 samreshu.pro", text, flags=re.I)
    return text


def publish(caption: str, images: list = None, video: Path = None) -> list:
    """Публикует пост сразу (через ~минуту). Возвращает список «куда ушло»."""
    done = []
    for social, gid, name in targets():
        attachments = []
        if video:
            for a in _upload(Path(video), gid, "video"):
                if social == "vk" and a.get("type") == "video":
                    a["type_video"] = "short_video"  # VK Клип
                attachments.append(a)
        if video and not any(a.get("type") == "video" for a in attachments):
            raise RuntimeError("Впостер принял видео, но не как видео: "
                               + json.dumps(attachments, ensure_ascii=False)[:400])
        for img in images or []:
            attachments += _upload(Path(img), gid, "image")
        body = {"id_group": gid, "type_social": social, "post_day": "oneraz",
                "message": adapt_caption(caption), "attachments_json": attachments}
        if social == "vk" and len(images or []) > 1:
            body["primary_attachments_mode"] = 1  # карусель
        r = requests.post(f"{API}/posts.create", headers={**_h(), "Content-Type": "application/json"},
                          data=json.dumps(body, ensure_ascii=False).encode(), timeout=120)
        _check(r, "posts.create")
        done.append(f"{NAMES.get(social, social)} ({name})")
    return done
