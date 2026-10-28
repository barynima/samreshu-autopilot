"""Промокод за подписку через директ Instagram.

Человек пишет кодовое слово → проверяем, подписан ли он (is_user_follow_business) → отвечаем промокодом
или просьбой подписаться. Instagram разрешает отвечать в течение 24 часов после сообщения человека.
Запускается workflow «Директ: промокоды» каждые 10 минут. Состояние — dm_state.json.
"""
import datetime
import json
import os
from pathlib import Path

import requests
import yaml

from . import instagram, telegram

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "dm_state.json"
PROMO = ROOT / "promo.yaml"


def _state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"seen": [], "sent": [], "asked": [], "error": ""}


def _save(st):
    st["seen"] = st["seen"][-2000:]
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def _reply(igsid: str, text: str):
    r = requests.post(f"{instagram.API}/me/messages", timeout=60,
                      json={"recipient": {"id": igsid}, "message": {"text": text}},
                      params={"access_token": instagram._token()})
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"ответ в директ: {data['error'].get('message')}")


def check(notify_chat=None) -> dict:
    promo = yaml.safe_load(PROMO.read_text(encoding="utf-8")) if PROMO.exists() else {}
    st = _state()
    stats = {"new": 0, "codes": 0, "asked": 0}
    if not promo.get("active"):
        return stats
    try:
        me = instagram._get("me", fields="user_id")["user_id"]
        convs = instagram._get("me/conversations", platform="instagram",
                               fields="participants,messages.limit(10){id,message,from,created_time}")
    except Exception as e:  # noqa: BLE001
        err = str(e)[:300]
        if notify_chat and st.get("error") != err:
            telegram.send_text(notify_chat, "⚠️ Не могу читать директ Instagram, промокоды не выдаются:\n" + err
                               + "\nСкорее всего, маркеру не хватает разрешения на сообщения "
                                 "(instagram_business_manage_messages). Напишите мне в Claude — разберёмся.")
        st["error"] = err
        _save(st)
        return stats
    st["error"] = ""
    st["debug"] = {"at": datetime.datetime.utcnow().isoformat(timespec="seconds"), "me": me,
                   "conversations": len(convs.get("data", [])),
                   "raw": json.dumps(convs, ensure_ascii=False)[:600]}
    today = datetime.date.today()
    until = datetime.date.fromisoformat(str(promo.get("until"))) if promo.get("until") else None
    for conv in convs.get("data", []):
        for m in (conv.get("messages") or {}).get("data", []):
            if m["id"] in st["seen"]:
                continue
            st["seen"].append(m["id"])
            sender = (m.get("from") or {}).get("id")
            body = (m.get("message") or "").lower()
            if not sender or str(sender) == str(me) or promo["keyword"].lower() not in body:
                continue
            stats["new"] += 1
            try:
                if until and today > until:
                    _reply(sender, promo["expired_text"])
                    continue
                prof = instagram._get(str(sender), fields="username,is_user_follow_business")
                if prof.get("is_user_follow_business"):
                    _reply(sender, promo["follower_text"])
                    if sender not in st["sent"]:
                        st["sent"].append(sender)
                    stats["codes"] += 1
                else:
                    _reply(sender, promo["not_follower_text"])
                    if sender not in st["asked"]:
                        st["asked"].append(sender)
                    stats["asked"] += 1
            except Exception as e:  # noqa: BLE001
                print(f"Директ {sender}: {e}")
    _save(st)
    return stats


def main():
    chat = os.environ.get("TG_CHAT_ID")
    if not chat and (ROOT / "state.json").exists():
        chat = json.loads((ROOT / "state.json").read_text(encoding="utf-8")).get("chat_id")
    stats = check(chat)
    print(stats)
    if os.environ.get("GITHUB_ACTIONS"):
        import subprocess
        run = lambda *a: subprocess.run(["git", *a], cwd=ROOT)
        run("add", "dm_state.json")
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode != 0:
            run("commit", "-m", "Директ: промокоды")
            for _ in range(3):
                if subprocess.run(["git", "push"], cwd=ROOT).returncode == 0:
                    break
                run("pull", "--rebase", "-X", "theirs")


if __name__ == "__main__":
    main()
