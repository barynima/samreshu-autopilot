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
    if r.status_code != 200 or "error" in data:
        raise RuntimeError(f"Впостер {what}: HTTP {r.status_code} {json.dumps(data, ensure_ascii=False)[:400]}")
    return data


def targets() -> list:
    """[(type_social, id_group, name)] — куда публикуем."""
    want = [t.strip() for t in (os.environ.get("VPOSTER_TARGETS") or "vk").split(",") if t.strip()]
    data = _check(requests.get(f"{API}/groups.getMy", headers=_h(), timeout=60), "groups.getMy")
    items = (data.get("response") or {}).get("items") or []
    pages = {}
    for block in items:
        for social, lst in block.items():
            pages.setdefault(social, []).extend(lst or [])
    out = []
    for social in want:
        lst = pages.get(social) or []
        if not lst:
            raise RuntimeError(f"Впостер: нет подключённых страниц типа «{social}». Подключите сообщество в кабинете.")
        pick = os.environ.get(f"VPOSTER_{social.upper()}_GROUP")
        page = next((p for p in lst if str(p["id"]) == str(pick)), None) if pick else lst[0]
        if not page:
            raise RuntimeError(f"Впостер: страница {pick} ({social}) не найдена среди подключённых.")
        out.append((social, str(page["id"]), page.get("name", "")))
    return out


def _upload(path: Path, id_group: str, kind: str) -> list:
    with open(path, "rb") as f:
        r = requests.post(f"{API}/uploads.uploadFile", headers=_h(), timeout=600,
                          files={"file": (path.name, f)}, data={"id_group": id_group, "type": kind})
    return (_check(r, "uploadFile").get("response") or {}).get("attachments") or []


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
