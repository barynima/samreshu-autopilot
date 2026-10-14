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


def veo_clip(image_bytes: bytes, motion: str, seconds: int = 6) -> bytes:
    creds = google._credentials()
    project = os.environ.get("GOOGLE_PROJECT_ID") or creds.project_id
    loc = google.LOCATION
    models = [os.environ["VEO_MODEL"]] if os.environ.get("VEO_MODEL") else VEO_MODELS
    prompt = (f"{motion} Soft matte 3D vinyl toy style, keep both characters exactly as in the image, "
              "static camera, no text, no subtitles.")
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


# ---------- сборка ----------

def assemble(shots: list, hook: str, out_path: Path, voices: bool = True) -> list:
    """shots: [{"clip": Path | None, "frame": bytes, "speaker", "line"}]. Возвращает список предупреждений."""
    warnings = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        parts = []
        for i, s in enumerate(shots):
            frame_png = tmp / f"frame{i}.png"
            render.fit_vertical(s["frame"]).save(frame_png)
            sub_png = tmp / f"sub{i}.png"
            sub_png.write_bytes(render.subtitle_overlay(s.get("speaker", ""), s.get("line", "")))
            hook_png = tmp / "hook.png"
            hook_png.write_bytes(render.hook_overlay(hook))

            voice = None
            if voices and s.get("line"):
                try:
                    voice = tmp / f"voice{i}.wav"
                    voice.write_bytes(tts(s["speaker"], s["line"]))
                except Exception as e:
                    voice = None
                    voices = False
                    warnings.append(f"Голоса отключены: {e}")

            clip = s.get("clip")
            clip_len = duration(clip) if clip else 0
            voice_len = duration(voice) if voice else 0
            if voice:
                dur = max(voice_len + 0.7, 2.5)
            elif s.get("line"):
                dur = max(2.5, min(6.0, len(s["line"]) / 14 + 1.2))  # время прочитать субтитр
            else:
                dur = clip_len or 5.0
            if clip and not voice and s.get("line"):
                dur = max(dur, min(clip_len, 6.0))

            v_in = (["-i", str(clip)] if clip else ["-loop", "1", "-framerate", str(FPS), "-i", str(frame_png)])
            a_in = (["-i", str(voice)] if voice else
                    ["-f", "lavfi", "-t", f"{dur:.2f}", "-i", "anullsrc=r=48000:cl=stereo"])
            fc = (f"[0:v]scale={render.RW}:{render.RH}:force_original_aspect_ratio=increase,"
                  f"crop={render.RW}:{render.RH},fps={FPS},setsar=1,"
                  f"tpad=stop_mode=clone:stop_duration={dur:.2f},trim=duration={dur:.2f},setpts=PTS-STARTPTS[v0];"
                  f"[v0][2:v]overlay=0:0[v1];")
            if i == 0:
                fc += f"[v1][3:v]overlay=0:0:enable='lt(t,2.4)'[v];"
            else:
                fc += "[v1]null[v];"
            delay = 200 if voice else 0
            fc += (f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={delay}|{delay},"
                   f"apad,atrim=duration={dur:.2f}[a]")
            part = tmp / f"part{i}.mp4"
            _run([*v_in, *a_in, "-i", str(sub_png), "-i", str(hook_png), "-filter_complex", fc,
                  "-map", "[v]", "-map", "[a]", "-t", f"{dur:.2f}",
                  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "21",
                  "-c:a", "aac", "-b:a", "128k", "-ar", "48000", str(part)])
            parts.append(part)

        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        _run(["-f", "concat", "-safe", "0", "-i", str(lst), "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
              "-profile:v", "high", "-crf", "21", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
              "-movflags", "+faststart", str(out_path)])
    return warnings
