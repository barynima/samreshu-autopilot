"""Описание персонажей «Сам и Надо» — одно место, откуда берут и тексты, и картинки."""

STYLE = (
    "Soft matte 3D vinyl toy style, simple rounded shapes, soft studio lighting, "
    "warm pastel colors, no text, no letters, no words, no speech bubbles anywhere in the image."
)

SAM = (
    "\"Sam\": a small cute gender-neutral school kid mascot, chunky rounded proportions with a "
    "slightly oversized head, warm coral hoodie (#FF7A59) with the hood ALWAYS up, face hidden in "
    "soft shadow showing only two glowing white oval eyes and simple eyebrows, no mouth, no nose, "
    "dark pants, white sneakers, small backpack with one pin badge."
)

NADO = (
    "\"Nado\": a big puffy grey-blue cloud creature (#8A94A6), about three times taller than Sam, "
    "short stubby arms often holding stacks of papers or brochures, round worried eyes, eyebrows "
    "raised in the middle, mouth in an anxious frown; looks strict but secretly scared. "
    "When he pressures he is huge and dark; when Sam asks an honest question he shrinks and gets lighter."
)

REF_PROMPTS = {
    "sam": (
        f"Character design sheet of {SAM} Front view, side view, back view, plus 4 expressions: "
        f"curious, doubtful, laughing, determined. Plain light background. {STYLE}"
    ),
    "nado": (
        f"Character design sheet of {NADO} Show three sizes side by side: huge and dark (pressuring), "
        f"medium, small and light (calm and friendly). Plain light background. {STYLE}"
    ),
}

# Короткий паспорт для текстовой модели
PASSPORT = """
Бренд «Сам решу» — ИИ-сервис профориентации для школьников 8–9 класса: помогает понять, что
нравится, найти направление и выбрать предметы ОГЭ. Цена 500 ₽, сайт samreshu.pro.

Персонажи:
• Сам — школьник в коралловом худи с капюшоном, без пола и возраста. Любопытный, спокойный,
  с юмором, немного сомневается в себе. Не бунтарь — тот, кто переспрашивает. Говорит коротко,
  на «ты», живым языком без натужного сленга. Главный инструмент — вопрос: «А почему?»,
  «А если нет?». Избегает форм прошедшего времени с родом («решил/решила»).
• Надо — большая серо-голубая туча чужих ожиданий. Не злодей и НИКОГДА не родитель/учитель —
  это «голос всех сразу»: бабушки, соседки, интернета. Говорит «надо», «все так делают»,
  ссылается на «тётю Свету» и непроверенные цифры. От честного вопроса Сама сдувается и
  признаётся, что просто боится за него. Иногда прав (сроки, документы, ОГЭ) — Сам это признаёт.

Ритм каждой сценки: давление Надо → один простой вопрос Сама → Надо сдувается → маленький шаг Сама.
Сам не побеждает и не прогоняет Надо — они договариваются.

Правила: говорим «школьники», а не «подростки». Не высмеиваем родителей и учителей. Не обещаем
«профессию мечты за 10 минут» — обещаем направление и понимание себя. Не решаем за школьника.
Не просим зрителей объяснять технологии или делиться экспертизой в комментариях.
Подпись к посту заканчивается крючком-клиффхэнгером, а не спокойным «до завтра».
"""
