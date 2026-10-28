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
TOY_FORMATS = {"reel_scene", "reel_pov", "reel_confess", "reel_text"}
LIFE_NAMING = ("Scenes are real life photos (not toys, not cartoons): describe people plainly — a mom in her early 40s, "
               "a teen girl or a teen boy in trendy 2026 clothes, sometimes a dad; their action, emotion and the room. "
               "Never mention Sam, Nado, toys or clouds in scene prompts.")
EXAMPLES = """«От „не знаю“ до *2–3 направлений* за один вечер»
«Миф: нужен ещё один тест на 40 вопросов. На самом деле: нужен *разговор*, где не страшно ответить „не знаю“»
«65% девятиклассников в 2026 году поступили в *колледжи и техникумы*. Колледж — нормальный путь, если выбран осознанно»
«Колледж или 10 класс? Решает *один вопрос*. И это не „где проще“»
«А если ребёнок на всё скажет „не знаю“? Это нормально. Чат задаёт вопросы *поменьше* — и ответы находятся»
Хорошо: одна мысль на фразу, конкретика (цифра, дата, вопрос), неожиданный поворот, разговорные слова мам.
Плохо: общие слова («важно поддерживать ребёнка»), канцелярит, лекция, три мысли в одной фразе."""
END_HINT = ("финальная карточка-крючок, до 9 слов: обещание КОНКРЕТНОЙ пользы, которая ждёт в описании и которой нет "
            "в ролике (например: «3 вопроса вместо „кем хочешь стать“ — в описании», «Что делать сегодня вечером — "
            "в описании»); без слова «подпишитесь»")
STORY_RULES = """Требования к ролику:
- Крючок описывает то, что зритель ВИДИТ в первом кадре, и цепляет маму за 1 секунду.
- Есть конфликт и поворот: давление → честный ответ/признание → неожиданный вывод. Без поворота ролик не нужен.
- Реплики — живая разговорная речь, как дома на кухне, не лозунги.
- Пол ребёнка — как в задании (сын или дочь); если не указан — пиши «ребёнок». Глаголы с родом — только в согласии с этим.
- Последний кадр должен перетекать в первый (ролик пересматривают по кругу)."""
SCENE_HINT = ("image prompt in ENGLISH for the first frame: who is where, poses, Nado's size "
              "(huge/medium/small), emotions, simple background; vertical 9:16")
MOTION_HINT = ("video prompt in ENGLISH, 1–2 sentences: what moves during ~5 seconds; "
               "characters keep exactly the same look; no camera moves, static shot")

LIFE_HINT = ("photo prompt in ENGLISH for a candid smartphone photo from the mom's real life: a modern renovated "
             "middle-class Russian apartment; ONE person (the mom, early 40s, OR the teen 14–15, girl or boy, face never visible) "
             "or at most two; one simple everyday moment the mom recognizes (mom reading a message on her phone at the kitchen "
             "table, teen in headphones at a desk seen from behind, mom with a mug by the window); name the exact emotion that "
             "fits the story (thinking, amused by a message, a bit tired, focused) — not a generic smile; simple pose, hands "
             "holding one object or resting; indoors only, no cars; daylight; no text in the picture")

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
    {{"text": "сообщение"}}
  ],
  "end": "{END_HINT}",
  "caption": "подпись"
}}
Цепочка: узнавание в диалоге → «с вами всё нормально» → один конкретный ход на сегодня → неожиданный вывод.
Пол ребёнка — как в задании (сын или дочь); если не указан — пиши «ребёнок». Глаголы с родом — только в согласии с этим.
Тон — светлый и тёплый, с лёгким юмором: мама улыбается, узнавая себя. Не пиши «страшно», «тревожно», «паника»,
«кошмар» и не нагнетай — сразу переводи в «это нормально» и интерес. Кадр — дневной и живой, эмоция естественная и
подходит к сюжету (задумалась, улыбнулась сообщению, немного устала), без дежурной улыбки «в камеру».
Если пол ребёнка не указан, мама в диалоге не использует глаголы с родом («Ну что, есть идеи, кем быть?»).
Кадр в ролике один — первый, под диалогом; дальше сообщения идут поверх него. От ролика к ролику чередуй героев и
комнаты (девочка или мальчик, кухня, детская, гостиная, прихожая; иногда папа), чтобы себя узнало больше мам.""".replace("{LIFE_HINT}", LIFE_HINT).replace("{LIFE_MOTION}", LIFE_MOTION),
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
Не пустые интерьеры и не «уютные натюрморты». Пол ребёнка — как в задании (сын или дочь); если не указан — пиши «ребёнок». Глаголы с родом — только в согласии с этим.""",
    "reel_story": """Reels «Кино» 24–30 секунд: короткая драма по кадрам, как сериал. Уровень — как образец ниже.
Драматургия: крючок-сцена за 2 секунды → узнавание (быстрый монтаж 2–3 «попыток») → ПОВОРОТ: показываем
ту же ситуацию глазами ребёнка → визуальная МЕТАФОРА (дверь крепнет, стена из «надо», и т. п.) → факт из списка
правил как поворот → маленький тёплый шаг вместо давления → финальный кадр рифмуется с первым (ролик зацикливается)
и задаёт маме вопрос. 7–11 кадров, у каждого точная длительность (1–6 с), сумма 20–30 с. Для юмора — темп быстрее
(кадры по 1–2 с, ритм склеек, смешной поворот). Переписку мамы и ребёнка показывай в кадре (pos chat) — мамы узнают её.
Каждый кадр — новый план: общий, средний, крупный, деталь; не повторяй одну и ту же картинку.
{
  "hook": "крючок первого кадра, до 9 слов",
  "cast": [  // 3–5 опорных изображений: мама, ребёнок (сын или дочь, как в задании), 1–3 места или предмета-символа
    {"id": "mom", "prompt": "EN photorealistic portrait: woman 41, modern well-groomed, tired kind eyes, hair in a claw clip, oversized beige knit sweater, smartwatch, in a modern apartment"},
    {"id": "teen", "prompt": "EN portrait of the 14-year-old teen: hair, trendy oversized hoodie colour, wireless headset; modern teen room"},
    {"id": "place", "prompt": "EN modern location/object that recurs: e.g. light oak eco-veneer interior door with frosted glass insert in a white hallway"}
  ],
  "shots": [
    {"sec": 2.0, "refs": ["mom", "place"], "visual": "EN кадр: крупность, кто/что, действие, свет — один ясный, цепляющий образ, простая поза",
     "motion": "EN медленное движение камеры/героя",
     "texts": [{"at": 0, "to": 2, "pos": "top", "lines": [{"t": "Я спросила сына, кем он хочет быть.", "w": "regular"}, {"t": "В *47-й* раз", "w": "bold"}]}],
     "vo": {"at": 0.2, "text": "закадр мамы, коротко, по-человечески", "voice": "mom"},
     "sfx": [{"at": 1.8, "type": "knock"}]}
  ],
  "cover_shot": 9,
  "cover_lines": [{"t": "«Не знаю» —", "w": "bold"}, {"t": "это не *«мне всё равно»*", "w": "bold"}],
  "music": "EN: тихое пианино/эмбиент, без вокала, теплеет к концу",
  "caption": "подпись"
}
pos: top — надпись в верхней трети (1–2 строки, до 7 слов в строке); center — реплика ребёнка; counter — плашка-счётчик
в углу («Попытка №47»); chat — пузыри переписки: {"pos": "chat", "messages": [{"who": "mom", "text": "…"}, {"who": "sam", "text": "хз"}]}.
voice: mom — закадр мамы; teen — одна-две реплики ребёнка («Не знаю»), звучат глухо, будто из-за двери.
sfx: tick, knock, click, bolt, send, thud. Закадр всего 12–16 секунд речи. Надписи не дублируют закадр слово в слово.
Кадры: современная квартира в новом доме, тёплый белый и мягкий синий свет, кинематографично, но не мрачно; без советских
интерьеров. Простые позы, без сложных действий руками; в одном кадре максимум два человека.
ЖЁСТКО: у КАЖДОГО кадра непустой refs; каждое место, где идёт действие (кухня, коридор школы, комната), — отдельный
элемент cast, иначе нейросеть нарисует другое место. НИКАКОГО читаемого текста в кадре: экран телефона или ноутбука —
со спины или пустой светящийся, переписку накладывай поверх через pos chat; без вывесок, тетрадей с надписями, бланков.
Юмор — через предметы и повтор (одинаковые кроссовки, одинаковые рюкзаки), а не через сложные сцены.
Нужен ПОВОРОТ в конце: ребёнок неожиданно сам говорит, что ему интересно, или мама меняет вопрос — и это работает.

ОБРАЗЕЦ («Попытка №47», 29 с): 1) 2 с — макро: кулак мамы застыл в сантиметре от двери, из щели синий свет;
«Я спросила сына, кем он хочет быть. / В 47-й раз»; стук на 1,8 с. 2) 2 с — у двери на стене детские отметки роста и
рядом свежие «палочки», как счёт дней; «Не знаю» голосом из-за двери; счётчик «Попытка №47». 3–5) по 1 с — ужин, машина,
мессенджер; счётчики «№12 — за ужином», «№29 — в машине», «№41 — в мессенджере», в мессенджере пузыри «Ты подумал, какие
предметы?» → «хз». 6) 3 с — поворот: сын сидит на полу спиной к той же двери и слушает; «А теперь — с той стороны двери».
7) 4 с — крупно: крутит шнурок худи; «„Не знаю“ — это не „мне всё равно“» → «Это „боюсь ответить не так — *и ты
расстроишься*“». 8) 3 с — метафора: на двери появляются засовы; «Чем чаще вопрос — тем крепче дверь»; щелчки засовов.
9) 6 с — вид сверху: мама и сын сидят спиной к одной двери с двух сторон; факт про нормальную стадию взросления.
10) 3 с — мама ставит у двери кружку чая; «Попытки №48 не будет. / Будет другой разговор». 11) 3 с — дверь приоткрыта,
кружка у порога (рифма с кадром 1); «Какая у вас попытка? / *Напишите номер*».""",
    "reel_kinetic": f"""Reels «Кинетический текст» 15–22 секунды: крупный текст оживает на экране — цифра считается, миф
зачёркивается, пункты появляются по одному. Кадр из жизни только на первом слайде.
{{
  "hook": "крючок на первом кадре, 4–9 слов: разрыв шаблона или острый вопрос; 1–2 слова в *звёздочках*",
  "hook_sub": "подзаголовок до 40 знаков, усиливает интригу",
  "first_scene": "{LIFE_HINT}; vertical 9:16, people in the upper and middle part, the bottom third calmer for the title",
  "kicker": "рубрика 1–3 слова",
  "slides": [  // ровно 3 слайда, минимум 2 РАЗНЫХ типа, в каждом 1–2 слова в *звёздочках*
    {{"type": "number", "number": "цифра ТОЛЬКО из списка цифр правил", "text": "что она значит, до 80 знаков", "note": "вывод, до 70 знаков"}},
    {{"type": "myth", "myth": "заблуждение мам, до 60 знаков", "truth": "как на самом деле, до 90 знаков"}},
    {{"type": "list", "title": "до 5 слов", "style": "numbers или checks", "items": ["ровно 3 пункта, каждый до 50 знаков"]}},
    {{"type": "qa", "q": "вопрос, до 60 знаков", "a": "ответ, до 110 знаков"}}
  ],
  "end": "{END_HINT}",
  "caption": "подпись"
}}
Если в правилах нет подходящей цифры — не используй тип number.""",
    "carousel": f"""Карусель 4:5 в журнальной вёрстке. Обложка — кадр из жизни с крупным заголовком.
{{
  "hook": "заголовок обложки, 4–9 слов, обещает конкретную пользу (число, вопрос, разрыв шаблона); 1–2 слова в *звёздочках*",
  "cover_sub": "подзаголовок обложки до 60 символов: зачем листать или сохранять",
  "cover_scene": "{LIFE_HINT}; vertical 4:5, people in the upper and middle part, the bottom third calmer for the title",
  "kicker": "рубрика 1–3 слова (например «ОГЭ без паники», «Вместо „кем станешь“»)",
  "cards": [  // 4–6 слайдов, минимум 3 РАЗНЫХ типа; одна мысль на слайд; в каждом 1–2 слова в *звёздочках*
    {{"type": "qa", "q": "вопрос мамы или вопрос ребёнку, до 70 знаков", "a": "ответ, до 140 знаков"}},
    {{"type": "number", "number": "цифра ТОЛЬКО из списка цифр правил (14%, 65%, 4,5–4,8, 1 марта…)", "text": "что она значит, до 90 знаков", "note": "что с этим делать, до 90 знаков"}},
    {{"type": "myth", "myth": "распространённое заблуждение мам, до 70 знаков", "truth": "как на самом деле, до 110 знаков"}},
    {{"type": "list", "title": "до 5 слов", "style": "numbers или checks", "items": ["2–4 пункта, каждый до 60 знаков"]}}
  ],
  "final": {{"title": "последний слайд: один шаг на сегодня или призыв сохранить, до 50 знаков", "sub": "до 80 знаков: что ещё ждёт в описании"}},
  "caption": "подпись"
}}
Если в правилах нет подходящей цифры — не используй тип number.""",
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

ПРОМПТЫ ДЛЯ КАРТИНОК И ВИДЕО (на английском): {NAMING if fmt in TOY_FORMATS else LIFE_NAMING}

ОБРАЗЦЫ ТОНА (так пишут лучшие посты аккаунта — коротко, конкретно, с одним выделенным словом):
{EXAMPLES}

{CAPTION_RULE}

Ответь ТОЛЬКО JSON по схеме, без пояснений и комментариев."""


def _validate(fmt: str, data: dict) -> dict:
    if fmt == "reel_chat" and not data.get("hook") and data.get("dialog"):
        data["hook"] = " — ".join(m.get("text", "") for m in data["dialog"]).replace("*", "")
    if not data.get("caption") or not data.get("hook"):
        raise ValueError("нет hook или caption")
    if fmt == "reel_story":
        shots = data.get("shots") or []
        if not data.get("hook") or not (6 <= len(shots) <= 12) or not data.get("cast"):
            raise ValueError("неполный кино-ролик")
        for sh in shots:
            if not sh.get("visual") or not sh.get("sec"):
                raise ValueError("кадр без описания или длительности")
            sh["sec"] = max(1.0, min(6.0, float(sh["sec"])))
        return data
    if fmt == "reel_kinetic":
        sl = data.get("slides") or []
        if not data.get("hook") or not data.get("first_scene") or not data.get("end") or not (2 <= len(sl) <= 4):
            raise ValueError("неполный кинетический ролик")
        need = {"qa": ("q", "a"), "number": ("number", "text"), "myth": ("myth", "truth"), "list": ("title", "items")}
        for c in sl:
            if c.get("type") not in need or not all(c.get(k) for k in need[c["type"]]):
                raise ValueError(f"неполный слайд {c.get('type')}")
        return data
    if fmt == "carousel":
        cards = data.get("cards") or []
        if not data.get("cover_scene") or not data.get("hook") or not (3 <= len(cards) <= 7) or not data.get("final"):
            raise ValueError("неполная карусель")
        need = {"qa": ("q", "a"), "number": ("number", "text"), "myth": ("myth", "truth"), "list": ("title", "items")}
        for c in cards:
            if c.get("type") not in need:  # старый вид карточки {title, text}
                c.update({"type": "qa", "q": c.get("title") or c.get("q", ""), "a": c.get("text") or c.get("a", "")})
            if not all(c.get(k) for k in need[c["type"]]):
                raise ValueError(f"неполный слайд {c.get('type')}")
        if isinstance(data["final"], str):
            data["final"] = {"title": data["final"], "sub": ""}
        return data
    if not data.get("end"):
        raise ValueError("нет финальной карточки end")
    if fmt == "reel_chat":
        dl = data.get("dialog") or []
        sl = data.get("slides") or []
        if len(dl) != 2 or [m.get("who") for m in dl] != ["mom", "sam"] or not all(m.get("text") for m in dl):
            raise ValueError("диалог должен быть: mom, затем sam")
        if not (2 <= len(sl) <= 5) or not data.get("first_scene") or any(not x.get("text") for x in sl):
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
            f"Призыв в конце подписи: {post.get('cta', 'Сохраните')}."
            + (f"\nРебёнок в сценке: {post['child']}." if post.get("child") else "")
            + ("\nПост от лица Марины (см. «Голос основательницы»), только правдивые факты о ней." if post.get("voice") == "founder" else ""))


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
7. Есть ли в посте что-то неожиданное — то, ради чего его сохранят? Честно: мама СОХРАНИТ это или перешлёт подруге?
   Если нет — не больше 7 баллов, даже если правила соблюдены.
8. Финал ролика обещает конкретную пользу в описании, а первая строка описания сразу её даёт? Если ролик ничем
   не зовёт читать описание — это минус 2 балла.
Будь жёстким: 9+ ставь только тому, что не стыдно показать лучшим SMM-командам. Средний пост — 5–6.
Эталон уровня 9–10 — такие фразы:
{examples}

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
        "systemInstruction": {"parts": [{"text": today + CRITIC.format(rules=RULES, examples=EXAMPLES)}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
    }, timeout=240)
    out = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
    res = _extract_json(out)
    res["score"] = int(res.get("score") or 0)
    return res


PHOTO_CHECK = """Ты — придирчивый фоторедактор. Перед тобой картинка, нарисованная нейросетью для поста. Она должна выглядеть
как настоящее фото с телефона. Найди брак. В «critical» пиши ТОЛЬКО то, что заметит обычная мама с первого взгляда на телефоне; мелкие
шероховатости — в «minor». Критичный брак:
- лишние или недостающие руки, ноги, пальцы; руки, сросшиеся с предметами; странные кисти;
- человек внутри мебели, между сиденьями, висит в воздухе, сидит мимо стула; тела сливаются;
- искажённое или «пластиковое» лицо; лицо подростка видно спереди;
- комната собрана из разных кусков: потолок, стены, окна, перспектива не сходятся;
- натянутая улыбка «в камеру» без причины или эмоция не подходит к сцене: {scene};
- надписи, буквы, логотипы; старый советский интерьер или разруха.
Ответь только JSON: {{"score": 1–10 (насколько похоже на настоящее фото без брака), "critical": ["найденный критичный брак"], "minor": ["мелочи"]}}"""


def check_photo(img: bytes, scene: str, allow_teen_face: bool = False) -> dict:
    """Проверка кадра на брак нейросети. ok — можно использовать."""
    import base64
    mime = "image/png" if img[:4] == b"\x89PNG" else "image/jpeg"
    model = os.environ.get("CRITIC_MODEL") or os.environ.get("GEMINI_TEXT_MODEL") or "gemini-2.5-pro"
    try:
        data = google.generate(model, {
            "contents": [{"role": "user", "parts": [
                {"text": PHOTO_CHECK.format(scene=scene[:400]).replace(
                    "искажённое или «пластиковое» лицо; лицо подростка видно спереди",
                    "искажённое или «пластиковое» лицо" if allow_teen_face else
                    "искажённое или «пластиковое» лицо; лицо подростка видно спереди")},
                {"inlineData": {"mimeType": mime, "data": base64.b64encode(img).decode()}}]}],
            "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
        }, timeout=180)
        out = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"] if not p.get("thought"))
        res = _extract_json(out)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "score": 0, "problems": [f"проверка не ответила: {e}"[:200]]}
    crit = [c for c in (res.get("critical") or []) if c]
    score = int(res.get("score") or 0)
    return {"ok": not crit and score >= 7, "score": score, "problems": crit + (res.get("minor") or [])[:2]}
