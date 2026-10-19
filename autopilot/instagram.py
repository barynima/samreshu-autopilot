"""Публикация карусели в Instagram через Instagram API with Instagram Login."""
import os
import time

import requests

API = "https://graph.instagram.com/" + (os.environ.get("IG_API_VERSION") or "v23.0")


def _token():
    return os.environ["IG_ACCESS_TOKEN"]


def _post(path, **params):
    r = requests.post(f"{API}/{path}", data={**params, "access_token": _token()}, timeout=120)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Instagram {path}: {data['error']}")
    return data


def _get(path, **params):
    r = requests.get(f"{API}/{path}", params={**params, "access_token": _token()}, timeout=60)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Instagram {path}: {data['error']}")
    return data


def _wait(container_id):
    for _ in range(20):
        status = _get(container_id, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Контейнер {container_id}: {status}")
        time.sleep(15)
    raise RuntimeError(f"Контейнер {container_id} так и не обработался")


def publish_carousel(image_urls: list[str], caption: str) -> str:
    user_id = _get("me", fields="user_id")["user_id"]
    children = []
    for url in image_urls:
        child = _post(f"{user_id}/media", image_url=url, is_carousel_item="true")["id"]
        _wait(child)
        children.append(child)
    carousel = _post(f"{user_id}/media", media_type="CAROUSEL", children=",".join(children),
                     caption=caption)["id"]
    _wait(carousel)
    media_id = _post(f"{user_id}/media_publish", creation_id=carousel)["id"]
    return _get(media_id, fields="permalink").get("permalink", media_id)


def refresh_token() -> str:
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": _token()}, timeout=60)
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"Не удалось продлить маркер: {data}")
    return data["access_token"]


def publish_reel(video_path, caption: str, cover_url: str = "", thumb_offset_ms: int = 1000) -> str:
    """Ролик загружаем в Instagram напрямую (resumable upload), без публичной ссылки на файл."""
    from pathlib import Path
    data = Path(video_path).read_bytes()
    user_id = _get("me", fields="user_id")["user_id"]
    params = dict(media_type="REELS", upload_type="resumable", caption=caption, share_to_feed="true")
    if cover_url:
        params["cover_url"] = cover_url
    else:
        params["thumb_offset"] = str(thumb_offset_ms)
    container = _post(f"{user_id}/media", **params)["id"]
    version = API.rsplit("/", 1)[-1]
    r = requests.post(f"https://rupload.facebook.com/ig-api-upload/{version}/{container}", data=data, timeout=600,
                      headers={"Authorization": f"OAuth {_token()}", "offset": "0", "file_size": str(len(data))})
    if r.status_code != 200 or not r.json().get("success", True):
        raise RuntimeError(f"Instagram: не удалось загрузить видео: HTTP {r.status_code} {r.text[:300]}")
    for _ in range(40):  # видео обрабатывается дольше картинок — ждём до 10 минут
        info = _get(container, fields="status_code,status")
        if info.get("status_code") == "FINISHED":
            break
        if info.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Instagram не принял ролик: {info.get('status') or info.get('status_code')}")
        time.sleep(15)
    else:
        raise RuntimeError("Instagram обрабатывал ролик дольше 10 минут")
    media_id = _post(f"{user_id}/media_publish", creation_id=container)["id"]
    return _get(media_id, fields="permalink").get("permalink", media_id)
