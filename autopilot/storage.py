"""Публичные ссылки на файлы для Instagram: Google Cloud Storage + подписанная ссылка (V4) на сутки.

Instagram API с входом через Instagram принимает видео и картинки только по публичной ссылке —
загрузить файл напрямую нельзя. Кладём файл в бакет проекта Google и отдаём подписанную ссылку.
Нужна роль сервисного аккаунта с правом записи в Cloud Storage (например, «Storage Admin»).
"""
import datetime
import hashlib
import os
from pathlib import Path
from urllib.parse import quote

import requests

from . import google

TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def _project():
    return os.environ.get("GOOGLE_PROJECT_ID") or google._credentials().project_id


def bucket_name() -> str:
    return os.environ.get("GCS_BUCKET") or f"{_project()}-samreshu-media"


def _auth():
    return {"Authorization": f"Bearer {google._credentials().token}"}


def _ensure_bucket(name: str):
    r = requests.get(f"https://storage.googleapis.com/storage/v1/b/{name}", headers=_auth(), timeout=30)
    if r.status_code == 200:
        return
    if r.status_code != 404:
        raise RuntimeError(f"Cloud Storage: нет доступа к бакету {name}: HTTP {r.status_code} {r.text[:200]}")
    body = {"name": name, "location": "US",
            "lifecycle": {"rule": [{"action": {"type": "Delete"}, "condition": {"age": 7}}]}}
    r = requests.post(f"https://storage.googleapis.com/storage/v1/b?project={_project()}", json=body,
                      headers=_auth(), timeout=30)
    if r.status_code not in (200, 409):
        raise RuntimeError(f"Cloud Storage: не удалось создать бакет: HTTP {r.status_code} {r.text[:200]}")


def _signed_url(bucket: str, obj: str, seconds: int = 86400) -> str:
    creds = google._credentials()
    now = datetime.datetime.now(datetime.timezone.utc)
    ts, ds = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
    scope = f"{ds}/auto/storage/goog4_request"
    uri = f"/{bucket}/{quote(obj, safe='/~')}"
    q = {
        "X-Goog-Algorithm": "GOOG4-RSA-SHA256",
        "X-Goog-Credential": f"{creds.service_account_email}/{scope}",
        "X-Goog-Date": ts,
        "X-Goog-Expires": str(seconds),
        "X-Goog-SignedHeaders": "host",
    }
    query = "&".join(f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted(q.items()))
    canonical = "\n".join(["GET", uri, query, "host:storage.googleapis.com\n", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(["GOOG4-RSA-SHA256", ts, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    sig = creds.signer.sign(to_sign.encode()).hex()
    return f"https://storage.googleapis.com{uri}?{query}&X-Goog-Signature={sig}"


def public_url(path: Path, name: str) -> str:
    """Загружает файл в бакет и возвращает ссылку, которую Instagram может скачать в течение суток."""
    bucket = bucket_name()
    _ensure_bucket(bucket)
    ctype = TYPES.get(Path(path).suffix.lower(), "application/octet-stream")
    with open(path, "rb") as f:
        r = requests.post(f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
                          params={"uploadType": "media", "name": name}, data=f, timeout=600,
                          headers={**_auth(), "Content-Type": ctype})
    if r.status_code != 200:
        raise RuntimeError(f"Cloud Storage: не удалось загрузить {name}: HTTP {r.status_code} {r.text[:200]}")
    return _signed_url(bucket, name)
