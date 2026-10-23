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


def send_photo(chat_id, data: bytes, caption: str = "", buttons=None):
    payload = {"chat_id": chat_id, "caption": caption[:1000]}
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    return _api("sendPhoto", data=payload, files={"photo": ("frame.jpg", data, "image/jpeg")})


def send_video(chat_id, data: bytes, caption: str = "", buttons=None, width=1080, height=1920, thumb: bytes = None,
               duration: int = None):
    """Размеры и обложку передаём явно: без них Telegram иногда показывает вертикальное видео приплюснутым."""
    payload = {"chat_id": chat_id, "caption": caption[:1000], "supports_streaming": "true",
               "width": str(width), "height": str(height)}
    if duration:
        payload["duration"] = str(duration)
    if buttons:
        payload["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    files = {"video": ("reel.mp4", data, "video/mp4")}
    if thumb:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(thumb)).convert("RGB")
        im.thumbnail((180, 320))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        files["thumbnail"] = ("thumb.jpg", buf.getvalue(), "image/jpeg")
    return _api("sendVideo", data=payload, files=files)


def send_document(chat_id, data: bytes, filename: str, caption: str = ""):
    return _api("sendDocument", data={"chat_id": chat_id, "caption": caption[:1000]},
                files={"document": (filename, data)})


def download(file_id: str) -> bytes:
    info = _api("getFile", json={"file_id": file_id})
    r = requests.get(f"https://api.telegram.org/file/bot{os.environ['TG_BOT_TOKEN']}/{info['file_path']}",
                     timeout=180)
    r.raise_for_status()
    return r.content
