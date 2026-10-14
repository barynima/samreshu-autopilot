"""Telegram-бот: присылает черновики и принимает кнопки «Публиковать / Переделать»."""
import json
import os

import requests


def _api(method: str, **kwargs):
    r = requests.post(
        f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/{method}", timeout=120, **kwargs
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data}")
    return data["result"]


def updates(offset: int, wait: int = 0):
    """wait > 0 — держим соединение до wait секунд, пока не придёт сообщение или нажатие."""
    r = requests.post(f"https://api.telegram.org/bot{os.environ['TG_BOT_TOKEN']}/getUpdates", timeout=wait + 30,
                      json={"offset": offset, "timeout": wait, "allowed_updates": ["message", "callback_query"]})
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram getUpdates: {data}")
    return data["result"]


def send_text(chat_id, text, buttons=None):
    payload = {"chat_id": chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return _api("sendMessage", json=payload)


def send_album(chat_id, files: list[bytes], caption: str = ""):
    media, uploads = [], {}
    for i, data in enumerate(files):
        key = f"f{i}"
        uploads[key] = (f"{key}.jpg", data, "image/jpeg")
        item = {"type": "photo", "media": f"attach://{key}"}
        if i == 0 and caption:
            item["caption"] = caption[:1000]
        media.append(item)
    return _api("sendMediaGroup", data={"chat_id": chat_id, "media": json.dumps(media)}, files=uploads)


def answer(callback_id, text=""):
    try:
        _api("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text})
    except Exception:
        pass  # кнопке больше 15 минут — ответить уже нельзя, это не страшно
