"""Reels: оживление кадров (Veo на Vertex AI), голоса (Google Text-to-Speech) и сборка ролика ffmpeg."""
import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from . import google, render

FPS = 30


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _run(args):
    r = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg: {r.stderr[-600:]}")


def duration(path: Path) -> float:
    r = subprocess.run([ffmpeg(), "-i", str(path)], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        line = line.strip()
        if line.startswith("Duration:"):
            h, m, s = line.split(",")[0].split()[1].split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"Не удалось узнать длительность {path.name}")


# ---------- Veo ----------

VEO_MODELS = ["veo-3.1-lite-generate-001", "veo-3.1-lite-generate-preview",
              "veo-3.1-fast-generate-001", "veo-3.1-fast-generate-preview"]


LIFE_VIDEO = ("Realistic candid home video shot on a phone, natural subtle movement, keep the people, faces, clothes "
              "and room exactly as in the image, faces that are hidden stay hidden, static camera, bright warm daylight, "
              "no text, no subtitles.")
TOY_VIDEO = ("Soft matte 3D vinyl toy style, keep both characters exactly as in the image, static camera, no text, "
             "no subtitles.")


def veo_clip(image_bytes: bytes, motion: str, seconds: int = 6, style: str = TOY_VIDEO) -> bytes:
    creds = google._credentials()
    project = os.environ.get("GOOGLE_PROJECT_ID") or creds.project_id
    loc = google.LOCATION
    models = [os.environ["VEO_MODEL"]] if os.environ.get("VEO_MODEL") else VEO_MODELS
    prompt = f"{motion} {style}"
    body = {
        "instances": [{"prompt": prompt,
                       "image": {"bytesBase64Encoded": base64.b64encode(image_bytes).decode(),
                                 "mimeType": "image/png"}}],
        "parameters": {"aspectRatio": "9:16", "durationSeconds": seconds, "sampleCount": 1,
                       "resolution": "720p", "generateAudio": False, "personGeneration": "allow_all"},
    }
    errors = []
    for model in models:
        base = (f"https://{loc}-aiplatform.googleapis.com/v1/projects/{project}/locations/{loc}"
                f"/publishers/google/models/{model}")
        h = {"Authorization": f"Bearer {google._credentials().token}"}
        r = requests.post(f"{base}:predictLongRunning", json=body, headers=h, timeout=120)
        if r.status_code == 404:
            errors.append(f"{model}: нет доступа")
            continue
        if r.status_code != 200:
            raise RuntimeError(f"Veo {model}: HTTP {r.status_code} {r.text[:300]}")
        op = r.json()["name"]
        for _ in range(60):
            time.sleep(10)
            h = {"Authorization": f"Bearer {google._credentials().token}"}
            p = requests.post(f"{base}:fetchPredictOperation", json={"operationName": op}, headers=h, timeout=60)
            data = p.json()
            if data.get("done"):
                if "error" in data:
                    raise RuntimeError(f"Veo: {data['error']}")
                videos = (data.get("response") or {}).get("videos") or []
                if not videos or not videos[0].get("bytesBase64Encoded"):
                    raise RuntimeError(f"Veo не вернул видео (возможно, сработал фильтр): {str(data)[:300]}")
                return base64.b64decode(videos[0]["bytesBase64Encoded"])
        raise RuntimeError("Veo: видео не готово за 10 минут")
    raise RuntimeError("Veo недоступен в проекте: " + "; ".join(errors))


# ---------- голоса ----------

VOICES = {
    "Сам": {"name": os.environ.get("VOICE_SAM") or "ru-RU-Wavenet-E", "pitch": 3.0, "rate": 1.05},
    "Надо": {"name": os.environ.get("VOICE_NADO") or "ru-RU-Wavenet-D", "pitch": -4.0, "rate": 0.95},
}


def tts(speaker: str, line: str) -> bytes:
    v = VOICES.get(speaker, VOICES["Сам"])
    h = {"Authorization": f"Bearer {google._credentials().token}"}
    project = os.environ.get("GOOGLE_PROJECT_ID") or google._credentials().project_id
    h["x-goog-user-project"] = project
    r = requests.post("https://texttospeech.googleapis.com/v1/text:synthesize", headers=h, timeout=60, json={
        "input": {"text": line},
        "voice": {"languageCode": "ru-RU", "name": v["name"]},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 48000,
                        "pitch": v["pitch"], "speakingRate": v["rate"]},
    })
    if r.status_code != 200:
        raise RuntimeError(f"Голос: HTTP {r.status_code} {r.text[:300]}")
    return base64.b64decode(r.json()["audioContent"])


# ---------- музыка ----------

MUSIC_PROMPT = ("bright upbeat acoustic pop instrumental: ukulele and light claps, playful marimba, warm bass, "
                "happy and optimistic, sunny morning mood, tempo around 110 bpm, background for a short social video")


def music(prompt: str = MUSIC_PROMPT) -> bytes:
    """Фоновая музыка без слов через Lyria на Vertex AI (WAV ~30 с)."""
    creds = google._credentials()
    project = os.environ.get("GOOGLE_PROJECT_ID") or creds.project_id
    loc = google.LOCATION
    model = os.environ.get("MUSIC_MODEL") or "lyria-002"
    url = (f"https://{loc}-aiplatform.googleapis.com/v1/projects/{project}/locations/{loc}"
           f"/publishers/google/models/{model}:predict")
    r = requests.post(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=180, json={
        "instances": [{"prompt": prompt, "negative_prompt": "vocals, singing, heavy drums, aggressive, distorted"}],
        "parameters": {"sample_count": 1}})
    if r.status_code != 200:
        raise RuntimeError(f"Музыка (Lyria): HTTP {r.status_code} {r.text[:300]}")
    pred = (r.json().get("predictions") or [{}])[0]
    b64 = pred.get("bytesBase64Encoded") or pred.get("audioContent")
    if not b64:
        raise RuntimeError(f"Музыка (Lyria): пустой ответ {str(pred)[:200]}")
    return base64.b64decode(b64)


# ---------- сборка ----------

SILENCE = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
ENC = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "20",
       "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]


def read_time(text: str) -> float:
    return max(2.6, min(5.5, len(text) / 13 + 1.6))


def _still(img_png: Path, overlay_png: Path, dur: float, out: Path, zoom: bool = True):
    """Картинка + надпись. zoom — очень медленное приближение (без смены ракурса)."""
    n = int(dur * FPS)
    if zoom:
        base = (f"[0:v]scale={render.RW * 2}:{render.RH * 2},"
                f"zoompan=z='1+0.05*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:"
                f"s={render.RW}x{render.RH}:fps={FPS},setsar=1[b];")
    else:
        base = f"[0:v]scale={render.RW}:{render.RH},fps={FPS},setsar=1,loop={n}:1:0[b];"
    fc = base + "[b][2:v]overlay=0:0,trim=duration={d:.2f},setpts=PTS-STARTPTS[v]".format(d=dur)
    _run(["-i", str(img_png), *SILENCE, "-i", str(overlay_png), "-filter_complex", fc,
          "-map", "[v]", "-map", "1:a", "-t", f"{dur:.2f}", *ENC, str(out)])


def _end_part(tmp: Path, text: str, out: Path, dur: float = 3.2):
    card = tmp / "end.png"
    card.write_bytes(render.end_card(text))
    empty = tmp / "empty.png"
    empty.write_bytes(render.empty_overlay())
    _still(card, empty, dur, out, zoom=False)


def _finish(parts: list, tmp: Path, out_path: Path, with_music: bool) -> list:
    warnings = []
    lst = tmp / "list.txt"
    lst.write_text("".join(f"file '{p}'\n" for p in parts))
    joined = tmp / "joined.mp4"
    _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-r", str(FPS), *ENC, str(joined)])
    track = None
    if with_music:
        try:
            track = tmp / "music.wav"
            track.write_bytes(music())
        except Exception as e:
            track = None
            warnings.append(f"Ролик без музыки: {e}")
    if track:
        d = duration(joined)
        fc = (f"[1:a]aloop=loop=-1:size=2000000000,atrim=duration={d:.2f},"
              f"afade=t=in:d=0.6,afade=t=out:st={max(0, d - 1.8):.2f}:d=1.8,volume=0.9[m];"
              f"[0:a][m]amix=inputs=2:duration=first:normalize=0[a]")
        _run(["-i", str(joined), "-i", str(track), "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)])
    else:
        _run(["-i", str(joined), "-c", "copy", "-movflags", "+faststart", str(out_path)])
    return warnings


def assemble(shots: list, hook: str, out_path: Path, voices: bool = False, end_text: str = "",
             with_music: bool = True) -> list:
    """Сценка с персонажами. shots: [{"clip": Path | None, "frame": bytes, "speaker", "line"}]."""
    warnings = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        hook_png = tmp / "hook.png"
        hook_png.write_bytes(render.hook_overlay(hook))
        for i, s in enumerate(shots):
            frame_png = tmp / f"frame{i}.png"
            render.fit_vertical(s["frame"]).save(frame_png)
            sub_png = tmp / f"sub{i}.png"
            sub_png.write_bytes(render.subtitle_overlay(s.get("speaker", ""), s.get("line", "")))

            voice = None
            if voices and s.get("line"):
                try:
                    voice = tmp / f"voice{i}.wav"
                    voice.write_bytes(tts(s["speaker"], s["line"]))
                except Exception as e:
                    voice, voices = None, False
                    warnings.append(f"Голоса отключены: {e}")

            clip = s.get("clip")
            clip_len = duration(clip) if clip else 0
            need = read_time(s.get("line", "")) if s.get("line") else 4.0
            if i == 0:
                need += 3.2  # сначала крючок, потом субтитр на том же месте
            if voice:
                need = max(need, duration(voice) + 0.8)
            dur = max(need, min(clip_len, 6.0)) if clip else need

            v_in = (["-i", str(clip)] if clip else ["-loop", "1", "-framerate", str(FPS), "-i", str(frame_png)])
            a_in = ["-i", str(voice)] if voice else SILENCE
            fc = (f"[0:v]scale={render.RW}:{render.RH}:force_original_aspect_ratio=increase,"
                  f"crop={render.RW}:{render.RH},fps={FPS},setsar=1,"
                  f"tpad=stop_mode=clone:stop_duration={dur:.2f},trim=duration={dur:.2f},setpts=PTS-STARTPTS[v0];"
                  )
            if i == 0:
                fc += "[v0][3:v]overlay=0:0:enable='lt(t,3.2)'[v1];[v1][2:v]overlay=0:0:enable='gte(t,3.2)'[v];"
            else:
                fc += "[v0][2:v]overlay=0:0[v];"
            delay = 200 if voice else 0
            fc += (f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={delay}|{delay},"
                   f"apad,atrim=duration={dur:.2f}[a]")
            part = tmp / f"part{i}.mp4"
            _run([*v_in, *a_in, "-i", str(sub_png), "-i", str(hook_png), "-filter_complex", fc,
                  "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}", *ENC, str(part)])
            parts.append(part)
        if end_text:
            end = tmp / "end.mp4"
            _end_part(tmp, end_text, end)
            parts.append(end)
        warnings += _finish(parts, tmp, out_path, with_music)
    return warnings


def assemble_slides(slides: list, end_text: str, out_path: Path, with_music: bool = True) -> list:
    """Текстовый Reels. slides: [{"image": bytes, "text": str}] — первый слайд это крючок."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        for i, sl in enumerate(slides):
            img = tmp / f"img{i}.png"
            render.fit_vertical(sl["image"]).save(img)
            over = tmp / f"over{i}.png"
            over.write_bytes(render.slide_overlay(sl["text"], big=(i == 0)))
            part = tmp / f"part{i}.mp4"
            _still(img, over, min(read_time(sl["text"]), 4.5), part)
            parts.append(part)
        if end_text:
            end = tmp / "end.mp4"
            _end_part(tmp, end_text, end)
            parts.append(end)
        return _finish(parts, tmp, out_path, with_music)


def _still_multi(img_png: Path, overlays: list, dur: float, out: Path, zoom: bool = True, clip: Path = None):
    """Картинка (или видеоклип) + несколько прозрачных слоёв, каждый в своём окне времени [(png, start, end)]."""
    n = int(dur * FPS)
    if clip:
        fc = (f"[0:v]scale={render.RW}:{render.RH}:force_original_aspect_ratio=increase,crop={render.RW}:{render.RH},"
              f"fps={FPS},setsar=1,eq=saturation=1.12:brightness=0.03,"
              f"tpad=stop_mode=clone:stop_duration={dur:.2f},trim=duration={dur:.2f},setpts=PTS-STARTPTS[b0];")
        img_png = clip
    elif zoom:
        fc = (f"[0:v]scale={render.RW * 2}:{render.RH * 2},"
              f"zoompan=z='1+0.05*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:"
              f"s={render.RW}x{render.RH}:fps={FPS},setsar=1[b0];")
    else:
        fc = f"[0:v]scale={render.RW}:{render.RH},fps={FPS},setsar=1,loop={n}:1:0[b0];"
    inputs = ["-i", str(img_png), *SILENCE]
    for i, (png, start, end) in enumerate(overlays):
        inputs += ["-i", str(png)]
        fc += f"[b{i}][{i + 2}:v]overlay=0:0:enable='between(t,{start:.2f},{end:.2f})'[b{i + 1}];"
    fc += f"[b{len(overlays)}]trim=duration={dur:.2f},setpts=PTS-STARTPTS[v]"
    _run([*inputs, "-filter_complex", fc, "-map", "[v]", "-map", "1:a", "-t", f"{dur:.2f}", *ENC, str(out)])


def assemble_chat(slides: list, end_text: str, out_path: Path, with_music: bool = True) -> list:
    """Reels-переписка. slides[0]: {"image", "dialog": [{"who":"mom",...},{"who":"sam",...}]};
    остальные: {"image", "text"} — сообщения от «Сам решу». Каждое сообщение появляется после «печатает…»."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        for i, sl in enumerate(slides):
            img = tmp / f"img{i}.png"
            from PIL import ImageEnhance
            pic = render.fit_vertical(sl["image"])
            pic = ImageEnhance.Brightness(ImageEnhance.Color(pic).enhance(1.15)).enhance(1.06)  # теплее и светлее
            pic.save(img)
            layers = []
            if sl.get("dialog"):
                mom, sam = sl["dialog"][0], sl["dialog"][1]
                dur = max(4.0, 1.7 + render_read(sam["text"]) + 0.8)
                a, b, c = tmp / f"a{i}.png", tmp / f"b{i}.png", tmp / f"c{i}.png"
                a.write_bytes(render.chat_overlay([mom]))
                b.write_bytes(render.chat_overlay([mom], typing="sam"))
                c.write_bytes(render.chat_overlay([mom, sam]))
                layers = [(a, 0.15, 0.8), (b, 0.8, 1.7), (c, 1.7, dur + 1)]
            else:
                msg = {"who": "brand", "text": sl["text"]}
                dur = 0.7 + min(read_time(sl["text"]), 4.5)
                a, b = tmp / f"a{i}.png", tmp / f"b{i}.png"
                a.write_bytes(render.chat_overlay([], typing="brand"))
                b.write_bytes(render.chat_overlay([msg]))
                layers = [(a, 0.0, 0.7), (b, 0.7, dur + 1)]
            part = tmp / f"part{i}.mp4"
            _still_multi(img, layers, dur, part, clip=sl.get("clip"))
            parts.append(part)
        if end_text:
            end = tmp / "end.mp4"
            _end_part(tmp, end_text, end)
            parts.append(end)
        return _finish(parts, tmp, out_path, with_music)


def render_read(text: str) -> float:
    return max(1.4, min(3.5, len(text) / 14 + 0.8))
