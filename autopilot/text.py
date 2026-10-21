"""Сценарий поста по стратегии: пишет Gemini (через Vertex AI) или Claude через Cloud.ru,
затем отдельный проход проверяет его по чек-листу гида и переписывает слабые места."""
import json
import os
import re
from pathlib import Path

import requests

from . import google

RULES = (Path(__file__).resolve().parent.parent / "strategy" / "rules.md").read_text(encoding="utf-8")

NAMING = ("In scene and motion prompts ALWAYS call the characters exactly 'Sam (a small vinyl toy figure in a coral "
          "hoodie with the hood up)' and 'Nado (a big puffy grey-blue cloud creature)'. NEVER use the words teenager, "
          "teen, kid, child, boy, girl, adult, person, he, she — video models block them or redraw the characters. "
          "Sam has no mouth: no lip movement, show emotion with eyes, eyebrows and body. "
          "All shots of one video happen in ONE place (same room, same light): describe that place identically "
          "in every scene. Nado must be visible in every shot where Nado speaks. ")
END_HINT = ("финальная карточка-крючок, до 9 слов: обещание КОНКРЕТНОЙ пользы, которая ждёт в описании и которой нет "
            "в ролике (например: «3 вопроса вместо „кем хочешь стать“ — в описании», «Что делать сегодня вечером — "
            "в описании»); без слова «подпишитесь»")
STORY_RULES = """Требования к ролику:
- Крючок описывает то, что зритель ВИДИТ в первом кадре, и цепляет маму за 1 секунду.
- Есть конфликт и поворот: давление → честный ответ/признание → неожиданный вывод. Без поворота ролик не нужен.
- Реплики — живая разговорная речь, как дома на кухне, не лозунги.
- О ребёнке не пиши «он» или «она»: Сам без пола. Пиши «ребёнок», «мой», «наш».
- Последний кадр должен перетекать в первый (ролик пересматривают по кругу)."""
SCENE_HINT = ("image prompt in ENGLISH for the first frame: who is where, poses, Nado's size "
              "(huge/medium/small), emotions, simple background; vertical 9:16")
MOTION_HINT = ("video prompt in ENGLISH, 1–2 sentences: what moves during ~5 seconds; "
               "characters keep exactly the same look; no camera moves, static shot")

LIFE_HINT = ("photo prompt in ENGLISH for a candid smartphone photo from the mom's real life: a modern renovated "
             "middle-class Russian apartment; the mom (early 40s, well-kept) and/or the teen (14–15, a girl or a boy, trendy 2026 clothes, "
             "face never visible — back view or turned away); one clear everyday moment the mom recognizes, shown in a warm, light, slightly funny way "
             "(teen in headphones at a sunny desk, phone at breakfast, mom laughing at a teen's answer, talk in a bright kitchen); "
             "daytime, bright, smiling or curious faces; no text in the picture")

LIFE_MOTION = ("video prompt in ENGLISH, 1 sentence: natural everyday movement for ~4 seconds in this photo (someone laughs, "
               "types, turns a page, shrugs, steam rises from a mug); NEVER use words teen, teenager, kid, child, boy, girl, "
               "minor — say 'a person in a green hoodie', 'the woman'; faces that are hidden stay hidden")

SCHEMAS = {
    "reel_scene": f"""Reels «Сценка» 12–20 секунд: 3 коротких плана спора Сама и Надо.
{{
  "hook": "крючок на экране с первой секунды, 3–7 слов",
  "shots": [  // ровно 3
    {{"speaker": "Надо" | "Сам", "line": "реплика по-русски, до 60 символов, звучит вслух",
      "scene": "{SCENE_HINT}", "motion": "{MOTION_HINT}"}}
  ],
  "end": "{END_HINT}",
  "caption": "подпись"
}}
Последний план — поворот: Надо сдувается и говорит честное, или Сам делает маленький шаг.
{STORY_RULES}""",
    "reel_confess": f"""Reels «Надо признаётся» 10–15 секунд: Надо крупным планом.
{{
  "hook": "крючок на экране с первой секунды, 3–7 слов",
  "shots": [  // ровно 2: сначала давит и раздувается, потом сдувается и тихо признаётся
    {{"speaker": "Надо", "line": "реплика по-русски, до 70 символов",
      "scene": "{SCENE_HINT}", "motion": "{MOTION_HINT}"}}
  ],
  "end": "{END_HINT}",
  "caption": "подпись"
}}
{STORY_RULES}""",
    "reel_pov": f"""Reels «POV» 7–10 секунд: одна сцена без слов, всё держится на надписи-крючке.
{{
  "hook": "надпись на экране, обычно начинается с «POV:», до 10 слов",
  "shots": [  // ровно 1
    {{"speaker": "", "line": "", "scene": "{SCENE_HINT}", "motion": "{MOTION_HINT}; loopable"}}
  ],
  "end": "{END_HINT}",
  "caption": "подпись"
}}""",
    "reel_chat": f"""Reels-переписка 12–18 секунд: кадры из жизни мамы + сообщения мессенджера поверх.
Первый кадр — короткий диалог: вопрос мамы и ответ ребёнка (ответ подписан «Сам» — это голос ребёнка).
Дальше сообщения пишет «Сам решу»: спокойная знающая подруга.
{{
  "dialog": [
    {{"who": "mom", "text": "вопрос или фраза мамы, как она пишет или говорит ребёнку, до 8 слов"}},
    {{"who": "sam", "text": "ответ ребёнка из словаря мам («не знаю», «мам, потом», «отстань», «я подумаю»), до 6 слов; можно *выделить* слово"}}
  ],
  "first_scene": "{{LIFE_HINT}}",
  "first_motion": "{{LIFE_MOTION}}",
  "slides": [  // 3–4 сообщения от «Сам решу», каждое — одна мысль, до 12 слов, 1–2 главных слова в *звёздочках*
    {{"text": "сообщение", "scene": "{{LIFE_HINT}}; a DIFFERENT family and apartment than the other slides", "motion": "{{LIFE_MOTION}}"}}
  ],
  "end": "{END_HINT}",
  "caption": "подпись"
}}
Цепочка: узнавание в диалоге → «с вами всё нормально» → один конкретный ход на сегодня → неожиданный вывод.
О ребёнке не пиши «он» или «она» — пиши «ребёнок». Мама в диалоге тоже не называет ребёнка по полу.
Тон — светлый и тёплый, с лёгким юмором: мама улыбается, узнавая себя. Не пиши «страшно», «тревожно», «паника»,
«кошмар» и не нагнетай — сразу переводи в «это нормально» и интерес. Сцены в кадрах — светлые, дневные, с улыбками.
Мама в диалоге не использует глаголы с родом («придумал», «решила») — только нейтральные формы
(«Ну что, есть идеи, кем быть?», «Как дела с выбором?»).
Кадры — РАЗНЫЕ семьи, чтобы себя узнало больше мам: чередуй девочек и мальчиков, разные квартиры и комнаты
(кухня, детская, прихожая, гостиная, машина у школы), разные мамы; иногда в кадре папа.""".replace("{LIFE_HINT}", LIFE_HINT).replace("{LIFE_MOTION}", LIFE_MOTION),
    "reel_text": f"""Reels-нарезка 12–18 секунд: Сам и Надо в выразительных позах + крупная фраза на каждом кадре.
{{
  "hook": "первая фраза на экране, 4–9 слов: мама узнаёт свою ситуацию за секунду",
  "first_scene": "image prompt in ENGLISH for the hook slide: Sam and/or Nado acting out the hook with a strong, readable emotion (surprise, panic, pride, doubt), one bold telling prop if needed; same room for all slides; vertical 9:16",
  "slides": [  // 3–4 слайда ПОСЛЕ крючка, каждый — одна мысль, до 12 слов
    {{"text": "фраза по-русски; 1–2 главных слова выдели *звёздочками*", "scene": "image prompt in ENGLISH: Sam and/or Nado acting out THIS line — a visual joke or metaphor, exaggerated pose and emotion, same room as the first slide"}}
  ],
  "end": "{END_HINT}",
  "caption": "подпись"
}}
Цепочка слайдов: узнавание → «с вами всё нормально» → один конкретный ход на сегодня → неожиданный вывод.
Каждый кадр должен останавливать палец: яркая эмоция персонажа, понятная без слов, визуальная шутка или метафора
(Надо раздувается от новости, Сам с кольцевой лампой в роли блогера, Надо прячется за подушкой).
Не пустые интерьеры и не «уютные натюрморты». О ребёнке не пиши «он» или «она» — пиши «ребёнок».""",
    "carousel": f"""Текстовая карусель 4:5.
{{
  "hook": "заголовок обложки, 3–6 слов",
  "cover_scene": "{LIFE_HINT}; vertical 4:5, the top third calm for a chat bubble with the title",
  "cards": [  // 5–7 карточек: одна мысль или один вопрос на карточку
    {{"title": "до 6 слов", "text": "до 160 символов"}}
  ],
  "final": "последняя карточка: один шаг и призыв, до 140 символов",
  "caption": "подпись"
}}""",
}

CAPTION_RULE = """Подпись (caption): до 800 знаков. ПЕРВАЯ строка сразу выполняет обещание финальной карточки ролика
(то самое, ради чего мама открыла описание: список вопросов, шаг на вечер, разбор), дальше по цепочке «с вами всё
нормально → один ход на сегодня → мягкое приглашение». Ролик НЕ раскрывает эту пользу полностью — только обещает. В конце вопрос, на который маме легко ответить про своего ребёнка, затем
призыв из задания, затем 3–5 хештегов (#самрешу #профориентация #огэ2027 #9класс и т.п.; нынешние 9-классники сдают ОГЭ в 2027 году).
Без цены и без слова «купить»."""


def _system(fmt: str) -> str:
    return f"""Ты — автор аккаунта «Сам решу» в Instagram. Пиши строго по правилам ниже.

{RULES}

ФОРМАТ ПОСТА:
{SCHEMAS[fmt]}

ПРОМПТЫ ДЛЯ КАРТИНОК И ВИДЕО (на английском): {NAMING}

{CAPTION_RULE}

Ответь ТОЛЬКО JSON по схеме, без пояснений и комментариев."""


def _validate(fmt: str, data: dict) -> dict:
    if fmt == "reel_chat" and not data.get("hook") and data.get("dialog"):
        data["hook"] = " — ".join(m.get("text", "") for m in data["dialog"]).replace("*", "")
    if not data.get("caption") or not data.get("hook"):
        raise ValueError("нет hook или caption")
    if fmt == "carousel":
        if not data.get("cover_scene") or not (3 <= len(data.get("cards") or []) <= 8) or not data.get("final"):
            raise ValueError("неполная карусель")
        return data
    if not data.get("end"):
        raise ValueError("нет финальной карточки end")
    if fmt == "reel_chat":
        dl = data.get("dialog") or []
        sl = data.get("slides") or []
        if len(dl) != 2 or [m.get("who") for m in dl] != ["mom", "sam"] or not all(m.get("text") for m in dl):
            raise ValueError("диалог должен быть: mom, затем sam")
        if not (2 <= len(sl) <= 5) or not data.get("first_scene") or any(not x.get("text") or not x.get("scene") for x in sl):
            raise ValueError("неполная переписка")
        return data
    if fmt == "reel_text":
        sl = data.get("slides") or []
        if not (2 <= len(sl) <= 5) or not data.get("first_scene") or any(not x.get("text") or not x.get("scene") for x in sl):
            raise ValueError("неполный текстовый Reels")
        return data
    need = {"reel_scene": 3, "reel_confess": 2, "reel_pov": 1}[fmt]
    shots = data.get("shots") or []
    if len(shots) != need:
        raise ValueError(f"нужно {need} планов, пришло {len(shots)}")
    for s in shots:
        if not s.get("scene") or not s.get("motion"):
            raise ValueError(f"план без scene/motion: {s}")
        if fmt != "reel_pov" and (s.get("speaker") not in ("Сам", "Надо") or not s.get("line")):
            raise ValueError(f"план без реплики: {s}")
    return data


def _extract_json(text: str) -> dict:
    text = re.sub(r"//[^\n\"]*\n", "\n", text)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"Модель не вернула JSON: {text[:300]}")
    return json.loads(match.group(0))


def _cloudru(system: str, user: str) -> str:
    base = os.environ["CLOUDRU_BASE_URL"].rstrip("/")
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['CLOUDRU_API_KEY']}"},
        json={"model": os.environ["TEXT_MODEL"], "temperature": 0.9, "max_tokens": 3000,
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        timeout=180,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _gemini(system: str, user: str, temperature=0.9) -> str:
    model = os.environ.get("GEMINI_TEXT_MODEL") or "gemini-2.5-pro"
    data = google.generate(model, {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": temperature, "responseMimeType": "application/json"},
    }, timeout=240)
    parts = data["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def _ask(system: str, user: str, temperature=0.9) -> str:
    if all(os.environ.get(k) for k in ("CLOUDRU_BASE_URL", "CLOUDRU_API_KEY", "TEXT_MODEL")):
        return _cloudru(system, user)
    return _gemini(system, user, temperature)


def _task(post: dict) -> str:
    who = "для подростка (на «ты»)" if post.get("audience") == "teen" else "для мамы 8–9-классника (на «вы»)"
    return (f"Пост {who}.\nРубрика: {post.get('rubric', '')}. Уровень лестницы: {post.get('level', '')}.\n"
            f"Крючок-ориентир: {post.get('hook', '')}\nСюжет: {post.get('idea', '').strip()}\n"
            f"Призыв в конце подписи: {post.get('cta', 'Сохраните')}.")


def write_post(post: dict, feedback: str = "") -> dict:
    fmt = post["format"]
    from datetime import date
    system = (f"Сегодня {date.today():%d.%m.%Y}. Нынешние 9-классники сдают ОГЭ в 2027 году, 8-классники — в 2028.\n"
              + _system(fmt))
    user = _task(post)
    if feedback:
        user += f"\n\nПрошлый вариант не понравился. Комментарий автора: {feedback}"
    last_err = None
    for _ in range(3):
        try:
            draft = _validate(fmt, _extract_json(_ask(system, user)))
            break
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
    else:
        raise RuntimeError(f"Не получилось написать пост: {last_err}")

    review = (f"{user}\n\nЧерновик:\n{json.dumps(draft, ensure_ascii=False)}\n\n"
              "Проверь черновик по чек-листу из правил и по разделу «Reels: что делает ролик виральным». "
              "Особенно: узнаёт ли мама своего ребёнка в крючке, нет ли канцелярита и ИИ-оборотов, нет ли "
              "выдуманных историй и цифр не из списка, звучат ли реплики как живая речь. Перепиши слабые места "
              "и верни исправленный JSON по той же схеме.")
    try:
        return _validate(fmt, _extract_json(_ask(system, review, temperature=0.5)))
    except (ValueError, json.JSONDecodeError, RuntimeError):
        return draft


# ---------- строгий критик ----------

CRITIC = """Ты — строгий главный редактор и арт-директор аккаунта «Сам решу». Ты видел тысячи постов для мам
и пропускаешь только лучшее. Аудитория: мама 35–50 лет, ребёнок в 8–9 классе. Оцени готовый пост — текст И картинки —
по правилам ниже и по этим вопросам:
1. Узнаёт ли мама свою ситуацию за 1 секунду по первому кадру и первой фразе?
2. Захочется ли досмотреть до конца и переслать мужу или подруге?
3. Настроение светлое и тёплое: день, улыбки, живые эмоции? Нет ли уныния, полумрака, серости, тревоги?
4. Картинки: современная квартира, ухоженные люди, подросток в актуальной одежде без видимого лица, нет стока, нет
   странных рук, лишних пальцев, букв и артефактов, нет советской разрухи?
5. Текст: живой, короткий, без канцелярита и ИИ-оборотов; нет «он/она» и глаголов с родом про ребёнка; нет страха,
   обещаний, цены, выдуманных историй и цифр не из списка?
6. Есть один понятный ход на сегодня и мягкий призыв?
7. Есть ли в посте что-то неожиданное — то, ради чего его сохранят?
8. Финал ролика обещает конкретную пользу в описании, а первая строка описания сразу её даёт? Если ролик ничем
   не зовёт читать описание — это минус 2 балла.
Будь жёстким: 8+ ставь только тому, что не стыдно показать лучшим SMM-командам. Средний пост — 5–6.

ПРАВИЛА АККАУНТА:
{rules}

Ответь только JSON: {{"score": целое 1–10, "verdict": "одна фраза для автора, по-русски",
"fixes": ["конкретная правка 1", "правка 2", "..."]}}"""


def critique(script: dict, pictures: list) -> dict:
    """Оценка готового поста: текст + картинки (jpeg/png bytes). Возвращает score, verdict, fixes."""
    import base64
    parts = [{"text": "Сценарий и подпись поста:\n" + json.dumps(script, ensure_ascii=False)}]
    for pic in pictures[:6]:
        mime = "image/png" if pic[:4] == b"\x89PNG" else "image/jpeg"
        parts.append({"inlineData": {"mimeType": mime, "data": base64.b64encode(pic).decode()}})
    model = os.environ.get("CRITIC_MODEL") or os.environ.get("GEMINI_TEXT_MODEL") or "gemini-2.5-pro"
    from datetime import date
    today = f"Сегодня {date.today():%d.%m.%Y}: нынешние 9-классники сдают ОГЭ в 2027 году, 8-классники — в 2028. "
    data = google.generate(model, {
        "systemInstruction": {"parts": [{"text": today + CRITIC.format(rules=RULES)}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }, timeout=240)
    out = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
    res = _extract_json(out)
    res["score"] = int(res.get("score") or 0)
    return res
