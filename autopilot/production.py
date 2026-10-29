"""Generation progress, bounded runtime and per-story casting (no publishing)."""
import contextlib
import hashlib
import json
import signal
import threading
import time
from datetime import date, datetime

_callback = None
_phase = "Подготовка сценария"


def progress(message):
    global _phase
    _phase = message
    print(f"[production] {message}", flush=True)
    if _callback:
        _callback(message)


class _Deadline(BaseException):
    pass  # must not be swallowed by provider retry/fallback handlers


@contextlib.contextmanager
def session(callback, seconds=2700, heartbeat_seconds=180):
    global _callback, _phase
    previous = _callback
    _callback, _phase = callback, "Подготовка сценария"
    stop = threading.Event()
    started = time.monotonic()

    def heartbeat():
        while not stop.wait(heartbeat_seconds):
            try:
                callback(f"Работа продолжается ({int((time.monotonic() - started) / 60)} мин): {_phase}")
            except Exception as exc:
                print(f"Progress delivery failed: {type(exc).__name__}", flush=True)

    worker = threading.Thread(target=heartbeat, daemon=True)
    old_handler = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise _Deadline()
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    worker.start()
    try:
        yield
    except _Deadline:
        raise RuntimeError("Достигнут лимит времени сборки. Готовые этапы сохранены; /post продолжит попытку.") from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)
        stop.set()
        worker.join(timeout=20)
        _callback = previous


def fingerprint(value):
    def encode(item):
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"Unsupported fingerprint value: {type(item).__name__}")
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=encode).encode()).hexdigest()


def cast_for(item, last_child=None, previous_family=None):
    child = "дочь" if last_child == "сын" else "сын" if last_child == "дочь" else item.get("child", "дочь")
    seed = int(fingerprint(item["id"])[:8], 16)
    mothers = [
        "a 36-year-old mom with short dark curly hair, denim jacket and comfortable sneakers",
        "a 44-year-old mom with shoulder-length auburn hair, glasses and a blue cotton shirt",
        "a 49-year-old mom with a silver-streaked bob, a green cardigan and expressive gestures",
        "a 39-year-old mom with a dark ponytail, a striped T-shirt and practical everyday clothes",
        "a 46-year-old mom with short fair hair, a burgundy sweater and a relaxed manner",
    ]
    interests = ["video editing", "biology and animals", "robotics", "music", "history", "team sports"]
    homes = ["a modest lived-in apartment with books and everyday clutter",
             "a bright older apartment with mismatched chairs and houseplants",
             "a small practical family kitchen with school bags nearby",
             "a family home with a narrow hallway and hobby materials on a desk"]
    teen = "14–15-year-old girl" if child == "дочь" else "14–15-year-old boy"
    mom_index = seed % len(mothers)
    if mothers[mom_index] == (previous_family or {}).get("mom"):
        mom_index = (mom_index + 1) % len(mothers)
    return {"child": child, "mom": mothers[mom_index], "teen": teen,
            "interest": interests[(seed // 5) % len(interests)], "home": homes[(seed // 30) % len(homes)]}


def family_instruction(family):
    return (f"Для ЭТОГО фильма новая семья: {json.dumps(family, ensure_ascii=False)}. "
            "Сын или дочь — строго как в задании; возраст 14–15 лет. Интерес не определяется полом. "
            "Внутри фильма фиксируй лицо, возраст, причёску и одежду каждого героя и каждую локацию через cast/refs. "
            "Это НЕ постоянные персонажи аккаунта. Допустимы обычные дома, прогулки и другие узнаваемые места. "
            "Один конкретный конфликт, поворот и завершённая мысль; никаких бессвязных красивых кадров. "
            "Эмоции естественны: не требуй улыбок во всех сценах. Не превращай историю в лекцию или запугивание.")
