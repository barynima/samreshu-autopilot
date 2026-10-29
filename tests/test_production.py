"""Regression checks for silent generation, casting and film quality. No network calls."""
import io
import json
import signal
import sys
import tempfile
import time
import unittest
import wave
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autopilot import cinematic, main, production, text


def wav(seconds):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        out.writeframes(b"\0\0" * int(seconds * 8000))
    return buf.getvalue()


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_timeout_cannot_be_swallowed_by_provider_fallback(self):
        handler = signal.getsignal(signal.SIGALRM)
        with self.assertRaisesRegex(RuntimeError, "лимит времени"):
            with production.session(Mock(), seconds=.03, heartbeat_seconds=.005):
                try:
                    time.sleep(.2)
                except Exception:
                    self.fail("Provider must not swallow generation deadline")
        self.assertEqual(signal.getsignal(signal.SIGALRM), handler)
        self.assertIsNone(production._callback)

    def test_heartbeat_reports_current_phase(self):
        callback = Mock()
        with production.session(callback, seconds=1, heartbeat_seconds=.01):
            production.progress("Кадр 3/8")
            time.sleep(.04)
        self.assertTrue(any("Кадр 3/8" in c.args[0] and "продолжается" in c.args[0]
                            for c in callback.call_args_list))

    def test_sons_and_daughters_alternate_without_repeating_mom(self):
        previous = None
        last = "сын"
        for i in range(12):
            family = production.cast_for({"id": f"episode-{i}"}, last, previous)
            self.assertNotEqual(family["child"], last)
            if previous:
                self.assertNotEqual(family["mom"], previous["mom"])
            last, previous = family["child"], family

    def test_cache_reuses_successful_stage(self):
        make = Mock(return_value=b"valid stage")
        self.assertEqual(cinematic._cached(self.root, "shot.jpg", make), b"valid stage")
        self.assertEqual(cinematic._cached(self.root, "shot.jpg", make), b"valid stage")
        make.assert_called_once()

    def test_yaml_date_has_same_fingerprint_as_iso_string(self):
        self.assertEqual(production.fingerprint({"item": {"date": date(2026, 10, 30)}}),
                         production.fingerprint({"item": {"date": "2026-10-30"}}))

    def test_all_real_plan_items_can_be_fingerprinted(self):
        posts = main.load_plan()
        self.assertTrue(any(isinstance(p.get("date"), date) for p in posts))
        for item in posts:
            with self.subTest(post=item["id"]):
                self.assertEqual(len(production.fingerprint({"item": item, "feedback": ""})), 64)

    def test_failed_stage_is_not_cached(self):
        make = Mock(side_effect=RuntimeError("bad frame"))
        with self.assertRaises(RuntimeError):
            cinematic._cached(self.root, "shot.jpg", make)
        self.assertFalse((self.root / "shot.jpg").exists())

    def test_wrong_scene_is_not_accepted_as_fallback(self):
        with patch.object(cinematic.images, "_call", return_value=b"image"), \
             patch.object(text, "check_photo", return_value={"ok": False, "score": 9, "problems": ["wrong age"]}):
            img, report = cinematic._shot_image({"visual": "15-year-old girl", "refs": []}, {})
        self.assertIsNone(img)
        self.assertIn("wrong age", report["problems"])

    def test_door_prompt_preserves_hinges(self):
        self.assertIn("hinge edge stays fixed", cinematic.motion_instruction({"visual": "a door", "motion": "opens"}))
        self.assertNotIn("hinge", cinematic.motion_instruction({"visual": "woman at table", "motion": "smiles"}))

    def test_wrong_hinge_motion_is_rejected(self):
        response = {"candidates": [{"content": {"parts": [{"text": json.dumps(
            {"critical": ["00:02 door opens from hinge side"], "verdict": "impossible mechanics"})}]}}]}
        with patch.object(text.google, "generate", return_value=response) as api:
            with self.assertRaisesRegex(ValueError, "hinge side"):
                text.check_motion(b"video", b"source image", {"visual": "door opens"})
        parts = api.call_args.args[1]["contents"][0]["parts"]
        self.assertTrue(any(p.get("inlineData", {}).get("mimeType") == "video/mp4" for p in parts))
        self.assertTrue(any(p.get("inlineData", {}).get("mimeType") == "image/jpeg" for p in parts))

    def test_voice_extends_scene_instead_of_cutting_speech(self):
        self.assertAlmostEqual(cinematic._voice_duration({"sec": 2, "vo": {"at": .2}}, wav(4)), 4.45)
        with self.assertRaisesRegex(RuntimeError, "сократите"):
            cinematic._voice_duration({"sec": 2}, wav(9))

    def test_undefined_cast_reference_is_rejected_before_images(self):
        script = {"hook": "Hook", "caption": "Caption", "cast": [{"id": "mom", "prompt": "Mom"}],
                  "shots": [{"visual": "Scene", "sec": 2, "refs": ["unknown"]} for _ in range(6)]}
        with self.assertRaisesRegex(ValueError, "refs"):
            text._validate("reel_story", script)

    def test_generation_failure_is_saved_and_resumable(self):
        state = {"done": [], "chat_id": 42}
        with patch.multiple(main, STATE=self.root / "state.json", git_sync=Mock(), load_plan=Mock(return_value=[]),
                            build_checked=Mock(side_effect=RuntimeError("provider unavailable"))), \
             patch.object(main.telegram, "send_text", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "Готовые этапы сохранены"):
                main.run_generation(state, {"id": "episode", "format": "reel_story"})
        self.assertEqual(state["generation"]["status"], "failed")
        self.assertEqual(json.loads((self.root / "state.json").read_text())["generation"]["status"], "failed")

    def test_retry_keeps_same_family(self):
        family = production.cast_for({"id": "episode"}, "сын")
        state = {"done": [], "chat_id": 42, "generation": {"post_id": "episode", "family": family}}
        builder = Mock(return_value={"id": "episode"})
        with patch.multiple(main, STATE=self.root / "state.json", git_sync=Mock(), load_plan=Mock(return_value=[]),
                            build_checked=builder), patch.object(main.telegram, "send_text", return_value={}):
            main.run_generation(state, {"id": "episode", "format": "reel_story"})
        self.assertEqual(builder.call_args.args[0]["family"], family)

    def test_film_review_uses_complete_video_with_audio(self):
        def transcode(args):
            Path(args[-1]).write_bytes(b"test full video")
        response = {"candidates": [{"content": {"parts": [{"text": json.dumps(
            {"score": 6, "verdict": "wrong scene", "critical": ["00:03 wrong age"], "fixes": ["replace shot"]})}]}}]}
        with patch("autopilot.video._run", side_effect=transcode), patch.object(text.google, "generate", return_value=response) as api:
            result = text.critique_video({"hook": "Hook"}, self.root / "film.mp4", [])
        parts = api.call_args.args[1]["contents"][0]["parts"]
        self.assertEqual(parts[-1]["inlineData"]["mimeType"], "video/mp4")
        self.assertTrue(result["critical"])

    def test_low_quality_film_is_blocked(self):
        job = {"id": "episode", "format": "reel_story", "video": "film.mp4", "script": {}}
        with patch.object(main, "build_post", return_value=job), patch.object(main, "save_job"), \
             patch.object(text, "critique_video", return_value={"score": 6, "critical": ["bad scene"], "fixes": []}):
            result = main.build_checked({"id": "episode", "format": "reel_story"})
        self.assertEqual(result["quality_status"], "blocked")

    def test_unavailable_video_reviewer_blocks_publication(self):
        job = {"id": "episode", "format": "reel_story", "video": "film.mp4", "script": {}}
        with patch.object(main, "build_post", return_value=job), patch.object(main, "save_job"), \
             patch.object(text, "critique_video", side_effect=RuntimeError("review unavailable")):
            result = main.build_checked({"id": "episode", "format": "reel_story"})
        self.assertEqual(result["quality_status"], "blocked")


if __name__ == "__main__":
    unittest.main()
