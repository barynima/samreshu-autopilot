"""Автопополнение контент-плана по гиду: когда впереди мало постов, дописывает неделю (7 постов).

Раскладка на 7 постов: 3 охватных (уровни 1–2), 2 экспертных (2–3), 2 продающих (3–5).
Темы берёт из гида (strategy/plan_guide.md) с учётом месяца учебного года и не повторяет прошлые крючки.
"""
import datetime
import re
from pathlib import Path

import yaml

from . import text

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "plan.yaml"
GUIDE = (ROOT / "strategy" / "plan_guide.md").read_text(encoding="utf-8")
FORMATS = {"reel_chat", "reel_kinetic", "carousel"}
CTAS = ["Сохраните", "Отправьте мужу", "Отправьте подруге", "Подпишитесь", "Ссылка в профиле"]

SYSTEM = f"""Ты контент-стратег аккаунта «Сам решу» (Instagram и ВКонтакте) для мам 8–9-классников.
Составь план на 7 следующих публикаций строго по гиду ниже.

Требования:
- Ровно 7 постов: 3 охватных (type «охват», level «1» или «1-2»), 2 экспертных (type «эксперт», level «2-3» или «3»),
  2 продающих (type «продажа», level «3»–«5»). Перемешай их, не ставь два продающих подряд.
- Форматы: reel_chat (ролик-переписка поверх кадров из жизни: сначала короткий диалог — фраза мамы и ответ ребёнка,
  потом 3–4 сообщения от «Сам решу», спокойной знающей подруги; других участников переписки нет),
  reel_kinetic (ролик с крупным оживающим текстом: цифра-счётчик, миф с зачёркиванием, шпаргалка — для фактов и
  разрушения мифов) и carousel (карусель 4:5 с пользой). На 7 постов: 3 reel_chat, 2 reel_kinetic, 2 carousel.
  Охватные — чаще переписка, экспертные — кинетические ролики и карусели.
- Темы подстраивай под месяц из календаря гида: сейчас {{month}}.
- Не повторяй темы и крючки из списка уже вышедших и запланированных.
- Если пост — сценка с конкретным ребёнком, укажи child: «сын» или «дочь» и чередуй их; иначе child не указывай.
- 1–2 поста в неделю могут быть от лица основательницы: voice: founder (только правдивые факты из правил).
- Вирусные рубрики, минимум 2 в неделю: «Ожидание и реальность» (мечты детства и кем стали), «Страшилки родителей»
  («дворником будешь!» против реальных цифр), «Собирательная история» (путь одного программиста/медсестры —
  с пометкой, что это собирательный образ), «Профессии, о которых вы не слышали», «На кого учиться, чтобы через
  7 лет быть нужным», «Кого заменит ИИ», «Что выбирают сейчас», «Перевод с подросткового», «Родительский чат»,
  «Хронология профессий мечты», снятие возражений («ИИ?», «он не станет», «а данные?», «тесты в школе были»).
- Крючок — сцена, цитата или конфликт до 10 слов: «Он выбрал обществознание. Потому что „там Лёха“».
- В idea опиши сюжет переписки (что пишет мама, что отвечает ребёнок, о чём сообщения «Сам решу») и что будет в подписи: один конкретный ход, который мама может
  сделать сегодня. Цифры — только из гида. Никаких выдуманных отзывов, кейсов, примеров результата и заработков.
  Никаких мультяшных персонажей. Не пугать.
- Продающие посты ведут на ссылку в профиле (samreshu.pro), кодовые слова не используй.
- cta — одно из: {", ".join(CTAS)}.
- id — латиницей через дефис, коротко и уникально.

Ответ — только JSON: {{"posts": [{{"id": "...", "format": "reel_chat|reel_kinetic|carousel", "rubric": "...", "level": "...",
"type": "охват|эксперт|продажа", "child": "сын|дочь или пусто", "voice": "founder или пусто",
"hook": "...", "idea": "...", "cta": "..."}}]}}

ГИД:
{GUIDE}
"""

MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]


def load() -> list:
    return yaml.safe_load(PLAN.read_text(encoding="utf-8"))["posts"]


def remaining(done: list, pending: str = None) -> int:
    return sum(1 for p in load() if p["id"] not in done and p["id"] != pending)


def extend(n_days: int = 7) -> list:
    """Дописывает в plan.yaml 7 постов на следующие дни. Возвращает новые посты."""
    posts = load()
    last = max(datetime.date.fromisoformat(str(p["date"])) for p in posts)
    start = max(last, datetime.date.today() - datetime.timedelta(days=1)) + datetime.timedelta(days=1)
    mid = start + datetime.timedelta(days=3)
    month = f"{MONTHS[mid.month - 1]} {mid.year}"
    used = "\n".join(f"- {p['hook']}" for p in posts)
    system = SYSTEM.replace("{month}", month)
    user = f"Даты: с {start:%d.%m.%Y} по {start + datetime.timedelta(days=n_days - 1):%d.%m.%Y}.\n\nУже были или запланированы:\n{used}"
    ids = {p["id"] for p in posts}
    last_err = None
    for _ in range(3):
        try:
            new = text._extract_json(text._gemini(system, user, temperature=0.8))["posts"]
            new = [p for p in new if p.get("format") in FORMATS and p.get("hook") and p.get("idea")][:n_days]
            if len(new) < 5:
                raise ValueError(f"мало постов: {len(new)}")
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    else:
        raise RuntimeError(f"Не удалось дописать план: {last_err}")
    out = []
    for i, p in enumerate(new):
        pid = re.sub(r"[^a-z0-9-]", "", str(p.get("id", "")).lower()) or f"post-{start:%m%d}-{i}"
        while pid in ids:
            pid += "-2"
        ids.add(pid)
        out.append({"id": pid, "date": (start + datetime.timedelta(days=i)).isoformat(), "format": p["format"],
                    "rubric": p.get("rubric", ""), "level": str(p.get("level", "")), "type": p.get("type", ""),
                    "hook": p["hook"], "idea": p["idea"],
                    "cta": p.get("cta") if p.get("cta") in CTAS else "Сохраните",
                    **({"child": p["child"]} if p.get("child") in ("сын", "дочь") else {}),
                    **({"voice": "founder"} if p.get("voice") == "founder" else {})})
    block = yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=110)
    block = "\n".join("  " + l if l else l for l in block.splitlines())
    PLAN.write_text(PLAN.read_text(encoding="utf-8").rstrip() + f"\n\n  # Дописано автопилотом {datetime.date.today():%d.%m.%Y}\n" + block + "\n",
                    encoding="utf-8")
    load()  # проверка, что файл читается
    return out
