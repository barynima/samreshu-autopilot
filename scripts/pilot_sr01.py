"""Isolated owner-approved pilot. No publishing credentials or bot state mutations."""
import base64
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

import requests
from autopilot import cinematic, google, images, production, telegram, text, video

OUT = Path("pilot-output/SR-PILOT-01")
OUT.mkdir(parents=True, exist_ok=True)
LEDGER = OUT / "budget.json"
CAP = 10.0
class StopProduction(BaseException):
    pass

def reserve(kind, dollars):
    data = json.loads(LEDGER.read_text()) if LEDGER.exists() else {"reserved_usd": 0, "calls": []}
    if data["reserved_usd"] + dollars > CAP:
        raise StopProduction("Pilot budget reservation limit reached; no further paid requests.")
    data["reserved_usd"] = round(data["reserved_usd"] + dollars, 4)
    data["calls"].append({"kind": kind, "reserved": dollars, "status": "submitted_or_unknown"})
    LEDGER.write_text(json.dumps(data, indent=2))

original_post = requests.post
def guarded_post(url, **kwargs):
    host = urlparse(url).hostname or ""
    body = kwargs.get("json") or {}
    if host.endswith("aiplatform.googleapis.com"):
        if url.endswith(":predictLongRunning"):
            model = url.split("/models/")[-1].split(":")[0]
            if model != "veo-3.1-fast-generate-001":
                raise StopProduction("Unapproved video model")
            params = body["parameters"]
            if params.get("sampleCount") != 1 or params.get("generateAudio"):
                raise StopProduction("Unapproved video parameters")
            reserve("video", float(params["durationSeconds"]) * 0.10)
        elif url.endswith(":generateContent"):
            config = body.setdefault("generationConfig", {})
            config["maxOutputTokens"] = min(config.get("maxOutputTokens", 2048), 2048)
            model = url.split("/models/")[-1].split(":")[0]
            if model not in ("gemini-2.5-flash-image", "gemini-3.8-flash"):
                raise StopProduction("Unapproved image/critic model")
            # Conservative allowance for the bounded image output or short clip check.
            reserve("image" if model.endswith("image") else "critic", 0.15)
            if model == "gemini-3.8-flash":
                url = url.replace("/locations/us-central1/", "/locations/global/")
        elif not url.endswith(":fetchPredictOperation"):
            raise StopProduction("Unrecognized Google operation")
    elif host == "texttospeech.googleapis.com":
        name = body["voice"]["name"]
        if name not in cinematic.NARRATOR + cinematic.TEEN:
            raise StopProduction("Unapproved voice")
        reserve("voice", 0.05)
    elif host != "api.telegram.org":
        raise StopProduction("Unapproved network destination")
    try:
        response = original_post(url, **kwargs)
        if host.endswith("aiplatform.googleapis.com") and response.status_code in (401, 403, 404):
            raise StopProduction(f"Google model access error {response.status_code}; no paid retries")
        if url.endswith(":predictLongRunning") and response.status_code == 200:
            op = response.json().get("name")
            if op:
                index = len(list(OUT.glob("operation-*.json")))
                (OUT/f"operation-{index}.json").write_text(json.dumps({"name":op}))
        return response
    except requests.RequestException:
        # No automatic retry after an uncertain paid submission.
        raise StopProduction("Network result uncertain; do not resubmit this run.") from None

mom = "Same 40-year-old mother, shoulder-length dark blonde hair, muted green cardigan, beige T-shirt and jeans."
daughter = "Same 15-year-old girl, brown ponytail, loose blue sweatshirt, age appropriate everyday clothes."
home = "Same modest lived-in family apartment in daylight. Kitchen with white fridge, plants and wooden table; adjoining bedroom has beige desk, laptop, corkboard photos, open doorway. No readable text or logos."
cast = [{"id":"mom","prompt":mom+" SOLO waist-up casting portrait, neutral plain light grey background, no room, no furniture, no other people. Hands outside frame. Natural face, neutral expression."},
        {"id":"daughter","prompt":daughter+" SOLO waist-up casting portrait, plain light grey background, no room, no furniture, no other people. Hands outside frame. Neutral expression."},
        {"id":"home","prompt":home+" Location reference ONLY, EMPTY rooms, no people, no portraits, no human figures."}]
def shot(sec, refs, visual, motion, line=None, voice="mom"):
    s = {"sec":sec,"refs":refs,"visual":visual,"motion":motion}
    if line:
        s["vo"]={"at":0.1,"text":line,"voice":voice}
        s["texts"]=[{"at":0.1,"to":sec,"pos":"top","lines":[{"t":line,"w":"regular"}]}]
    return s

script = {"hook":"Да ей вообще ничего не интересно","cast":cast,"cover_shot":1,"shots":[
    shot(4,["mom","home"],"Mother in kitchen, phone held near mouth recording voice message, mildly frustrated. "+mom,
         "Subtle natural breathing and small eye movement, fixed camera, phone stays still. No generated speech.",
         "Да ей вообще ничего не интересно."),
    shot(6,["daughter","home"],"Girl absorbed editing at laptop, headphones on ears, screen faces away, happy concentration. "+daughter,
         "Very small head movement while listening, hands resting still, no typing. No generated speech.",
         "Музыку убери. Тут сначала тишина — потом удар.","teen"),
    shot(4,["daughter","home"],"Same girl at same desk, pleased small smile after reviewing her edit.",
         "Small natural smile and nod, fixed camera, no hand manipulation. No generated speech.",
         "Вот. Теперь смешно.","teen"),
    shot(4,["mom","home"],"Mother in already open bedroom doorway looking toward daughter offscreen. Door is stationary.",
         "Mother glances toward daughter with questioning expression, mouth not visibly articulating. Stationary door.",
         "Ты опять просто в телефоне?"),
    shot(4,["daughter","home"],"Girl at same desk looks over shoulder toward mother, headphones around neck.",
         "Small glance toward doorway, natural reaction, hands stationary. No generated speech.",
         "Ага.","teen"),
    shot(4,["mom","home"],"Mother back at same kitchen table looking down thoughtfully at phone. Screen hidden, thumb already touching side of phone.",
         "Mother lowers phone slightly and reconsiders with small thoughtful expression. No finger or UI animation.")
]}
script["shots"][-1]["texts"]=[{"at":1,"to":4,"pos":"top","lines":[{"t":"Может, мы не всё замечаем?","w":"regular"}]}]

def main():
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise StopProduction("Workflow rerun prohibited: reconcile earlier costs and operations first.")
    if LEDGER.exists():
        raise StopProduction("Existing run ledger: automatic rerun prohibited.")
    LEDGER.write_text(json.dumps({"reserved_usd":2.50,"calls":[{"kind":"prior_runs_1_and_2_conservative_allowance","reserved":2.50}]}))
    for key,value in {"IMAGE_MODEL":"gemini-2.5-flash-image","CRITIC_MODEL":"gemini-3.8-flash",
                      "GEMINI_TEXT_MODEL":"gemini-3.8-flash"}.items():
        os.environ[key]=value
    # Disable alternate providers and fallback video models for deterministic budget.
    os.environ.pop("YANDEX_TTS_KEY", None)
    video.VEO_FAST = ["veo-3.1-fast-generate-001"]
    (OUT/"script.json").write_text(json.dumps(script,ensure_ascii=False,indent=2))
    cid = os.environ.get("TG_CHAT_ID") or str(json.loads(Path("state.json").read_text()).get("chat_id") or "")
    if not cid:
        raise StopProduction("No Telegram recipient; generation not started")
    telegram.send_text(cid, "SR-PILOT-01: начинаю отдельную сборку. Лимит $10. Публикации отключены.")
    def progress(message):
        print(message, flush=True)
        telegram.send_text(cid, "SR-PILOT-01: " + message)
    
    # Pilot-scoped cinematic quality criteria. Retain anatomy, continuity and scene checks.
    text.PHOTO_CHECK = """Ты проверяешь реалистичный кино-кадр или опорный эталон персонажа/локации.
Задание: {scene}
Оцени только видимые детали. Киносвет и оптическое размытие допустимы.
Обычная скромная квартира допустима; её стиль сам по себе не дефект.
Для SOLO casting portrait требуется ровно один заданный человек, нейтральный фон,
никаких других людей и мебели. Для EMPTY location reference людей быть не должно.
В сюжетных кадрах проверь состав людей, возраст, действие и согласованность эталонов.
Critical: лишние/деформированные конечности, слияния тел и мебели, невозможная
геометрия, явно искажённые лица, лишние персонажи, нечитаемые надписи, несоответствие
заданию. Не придумывай невидимые руки или детали; руки за кадром допустимы.
Ответ JSON: {{"score":1,"critical":[],"minor":[]}}.
score от 1 до 10; 7 и выше только при отсутствии существенного брака."""
    original_image_call = images._call
    original_check_photo = text.check_photo
    def save_image(*args, **kwargs):
        data = original_image_call(*args, **kwargs)
        folder = OUT/"diagnostics"
        folder.mkdir(exist_ok=True)
        index = len(list(folder.glob("generated-*")))
        suffix = ".png" if data[:4] == b"\\x89PNG" else ".jpg"
        (folder/f"generated-{index:03d}{suffix}").write_bytes(data)
        return data
    def save_photo_review(*args, **kwargs):
        review = original_check_photo(*args, **kwargs)
        folder = OUT/"diagnostics"
        folder.mkdir(exist_ok=True)
        index = len(list(folder.glob("review-*.json")))
        (folder/f"review-{index:03d}.json").write_text(json.dumps(review,ensure_ascii=False))
        return review

    with patch.object(requests,"post",guarded_post), patch.object(images,"_call",save_image), patch.object(text,"check_photo",save_photo_review), production.session(progress,seconds=2700):
        production.progress("Проверка доступа к актуальной модели до генерации кадров")
        google.generate("gemini-3.8-flash", {"contents":[{"role":"user","parts":[{"text":"Return only OK"}]}],"generationConfig":{"maxOutputTokens":32}}, timeout=60)
        result=cinematic.render_reel(script,OUT/"pilot.mp4",with_music=False,veo=True,cache_dir=OUT/"cache")
        review = text.critique_video(script, OUT/"pilot.mp4", result.get("timeline"))
        (OUT/"quality.json").write_text(json.dumps(review,ensure_ascii=False,indent=2))
        if review.get("critical"):
            raise StopProduction("Full-film quality check failed; see quality.json")
        (OUT/"render.json").write_text(json.dumps({k:v for k,v in result.items() if k not in ("cover","pictures")},ensure_ascii=False,indent=2))
        if cid:
            telegram.send_video(cid,(OUT/"pilot.mp4").read_bytes(),
                "SR-PILOT-01 v1 — экспериментальный фильм. Нужен просмотр целиком со звуком. Публикация не разрешена.")
    (OUT/"status.json").write_text(json.dumps({"status":"rendered_for_human_review","published":False}))
if __name__=="__main__":
    if os.environ.get("PILOT_RECOVERY_AUDIT") == "1":
        ledger = json.loads(LEDGER.read_text())
        print("RECOVERY_BUDGET=" + json.dumps(ledger), flush=True)
        print("RECOVERED_FILES=" + json.dumps([str(p.relative_to(OUT)) for p in OUT.rglob("*") if p.is_file()]), flush=True)
        raise SystemExit(0)
    try:
        main()
    except BaseException as exc:
        try:
            cid = os.environ.get("TG_CHAT_ID") or str(json.loads(Path("state.json").read_text()).get("chat_id") or "")
            if cid:
                telegram.send_text(cid, f"SR-PILOT-01: сборка остановлена ({type(exc).__name__}: {str(exc)[:350]}). Этапы и журнал расходов сохранены в GitHub. Автоповтор отключён.")
        except Exception:
            print("Telegram failure notification unavailable", flush=True)
        (OUT/"status.json").write_text(json.dumps({"status":"stopped","error_type":type(exc).__name__,"published":False}))
        raise
