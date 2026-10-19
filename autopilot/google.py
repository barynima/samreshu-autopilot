"""Доступ к Gemini через Vertex AI — так же, как в боте для интерьеров.

Ключ — JSON сервисного аккаунта Google целиком, в секрете GOOGLE_CREDENTIALS_JSON.
"""
import json
import os
import time

import requests

LOCATION = os.environ.get("VERTEX_LOCATION") or "us-central1"
_creds = None


def _credentials():
    global _creds
    if _creds is None:
        from google.oauth2.service_account import Credentials
        info = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
        _creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not _creds.valid:
        from google.auth.transport.requests import Request
        _creds.refresh(Request())
    return _creds


def generate(model: str, payload: dict, timeout: int = 300) -> dict:
    creds = _credentials()
    project = os.environ.get("GOOGLE_PROJECT_ID") or creds.project_id
    url = (f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/{LOCATION}"
           f"/publishers/google/models/{model}:generateContent")
    last = None
    for attempt in range(4):
        try:
            r = requests.post(url, json=payload, timeout=timeout,
                              headers={"Authorization": f"Bearer {_credentials().token}"})
        except (requests.ConnectionError, requests.Timeout) as e:  # Google иногда рвёт соединение
            last = f"сеть: {e}"
            time.sleep(15 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r.json()
        last = f"HTTP {r.status_code}: {r.text[:400]}"
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(15 * (attempt + 1))
            continue
        break
    raise RuntimeError(f"Vertex AI ({model}): {last}")
