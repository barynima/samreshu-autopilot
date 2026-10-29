"""Exercise the real approval functions without loading paid generation backends."""
import ast
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "autopilot" / "main.py"


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "posts" / "episode").mkdir(parents=True)
        (root / "posts" / "episode" / "image.jpg").write_bytes(b"original image")
        self.job = {"id": "episode", "format": "carousel", "hook": "Test", "stage": "ready",
                    "caption": "Caption", "files": ["posts/episode/image.jpg"]}
        self.state = {"pending": "episode", "done": [], "chat_id": 42, "awaiting_feedback": None}
        self.tg = Mock()
        self.tg.send_text.side_effect = lambda *a, **kw: {"message_id": self.tg.send_text.call_count}
        self.ig = Mock()
        self.ig.publish_carousel.return_value = "https://instagram.com/p/example"
        self.ns = dict(hashlib=hashlib, json=json, os=os, shutil=shutil, ROOT=root, POSTS=root / "posts", datetime=datetime, timezone=timezone,
                       telegram=self.tg, instagram=self.ig, time=Mock(), FORMAT_NAMES={}, MAX_TRIES=3,
                       save_state=Mock(), git_sync=Mock(), public_url=lambda f: "https://media/" + f,
                       chat=lambda s: s["chat_id"], load_job=lambda _: self.job, cleanup_media=Mock(),
                       top_up_plan=Mock(), next_post=lambda s: self.job, build_checked=lambda i: self.job,
                       vposter=Mock())
        tree = ast.parse(SOURCE.read_text())
        names = {"draft_fingerprint", "archive_draft", "restore_version", "show_versions", "send_draft", "publish", "_prepare", "handle"}
        selected = ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names],
                              type_ignores=[])
        exec(compile(selected, str(SOURCE), "exec"), self.ns)
        self.ns["save_job"] = self.save_job

    def save_job(self, job):
        self.job = job

    def show(self):
        self.ns["send_draft"](self.state, self.job)
        return self.state["draft_review"]["message_id"]

    def publish(self, message=None):
        return self.ns["publish"](self.state, "episode", message)

    def test_no_review_cannot_publish(self):
        with self.assertRaises(RuntimeError):
            self.publish(1)
        self.ig.publish_carousel.assert_not_called()

    def test_current_review_publishes_once_only_to_instagram(self):
        message = self.show()
        self.publish(message)
        self.ig.publish_carousel.assert_called_once_with(["https://media/posts/episode/image.jpg"], "Caption")
        self.ns["vposter"].publish.assert_not_called()
        with self.assertRaises(RuntimeError):
            self.publish(message)
        self.assertEqual(self.state["publication_attempts"]["episode"]["status"], "published")

    def test_changed_caption_or_media_cannot_publish(self):
        for change in ("caption", "media"):
            with self.subTest(change=change):
                message = self.show()
                if change == "caption":
                    self.job["caption"] += " changed"
                else:
                    (self.ns["ROOT"] / "posts/episode/image.jpg").write_bytes(b"new pixels")
                with self.assertRaises(RuntimeError):
                    self.publish(message)
        self.ig.publish_carousel.assert_not_called()

    def test_previous_review_button_is_invalid_after_resend(self):
        old = self.show()
        current = self.show()
        self.assertNotEqual(old, current)
        with self.assertRaises(RuntimeError):
            self.publish(old)
        self.publish(current)

    def test_feedback_blocks_publication(self):
        message = self.show()
        self.state["awaiting_feedback"] = "episode"
        with self.assertRaises(RuntimeError):
            self.publish(message)
        self.ig.publish_carousel.assert_not_called()

    def test_ambiguous_failure_blocks_retry(self):
        message = self.show()
        self.ig.publish_carousel.side_effect = TimeoutError("response lost")
        with self.assertRaises(TimeoutError):
            self.publish(message)
        with self.assertRaises(RuntimeError):
            self.publish(message)
        self.ig.publish_carousel.assert_called_once()
        self.assertEqual(self.state["publication_attempts"]["episode"]["status"], "started")

    def test_auto_publish_flag_cannot_bypass_review(self):
        self.state["pending"] = None
        with patch.dict(os.environ, {"AUTO_PUBLISH": "true"}):
            self.ns["_prepare"](self.state)
        self.assertIn("draft_review", self.state)
        self.ig.publish_carousel.assert_not_called()

    def test_entire_caption_is_shown(self):
        self.job["caption"] = "x" * 5000
        self.show()
        sent = [c.args[1] for c in self.tg.send_text.call_args_list]
        self.assertEqual("".join(s for s in sent if s and set(s) == {"x"}), self.job["caption"])

    def test_other_sender_cannot_approve(self):
        message = self.show()
        update = {"callback_query": {"id": "click", "data": "pub:episode", "from": {"id": 7},
                   "message": {"message_id": message, "chat": {"id": 42, "type": "private"}}}}
        self.ns["handle"](self.state, update)
        self.ig.publish_carousel.assert_not_called()

    def test_legacy_button_is_rejected(self):
        update = {"callback_query": {"id": "click", "data": "pub:episode", "from": {"id": 42},
                   "message": {"message_id": 123, "chat": {"id": 42, "type": "private"}}}}
        with self.assertRaises(RuntimeError):
            self.ns["handle"](self.state, update)
        self.ig.publish_carousel.assert_not_called()

    def test_restore_old_caption_and_media_then_publish(self):
        old_message = self.show()
        old_version = self.state["draft_review"]["version"]
        self.job["caption"] = "Worse caption"
        (self.ns["ROOT"] / "posts/episode/image.jpg").write_bytes(b"worse image")
        self.show()
        self.ns["restore_version"](self.state, old_version)
        self.assertEqual(self.job["caption"], "Caption")
        self.assertEqual((self.ns["ROOT"] / self.job["files"][0]).read_bytes(), b"original image")
        with self.assertRaises(RuntimeError):
            self.publish(old_message)
        self.publish(self.state["draft_review"]["message_id"])
        self.assertEqual(self.ig.publish_carousel.call_args.args[1], "Caption")

    def test_unchanged_draft_does_not_duplicate_archive(self):
        self.show()
        self.show()
        paths = list((self.ns["POSTS"] / "episode" / "versions").glob("*/post.json"))
        self.assertEqual(len(paths), 1)

    def test_version_menu_does_not_publish(self):
        self.show()
        self.job["caption"] = "Second version"
        self.show()
        self.ns["show_versions"](self.state)
        self.ig.publish_carousel.assert_not_called()
        buttons = self.tg.send_text.call_args.args[2]
        self.assertEqual(len(buttons), 2)

    def test_invalid_version_cannot_change_current_job(self):
        self.show()
        before = self.job.copy()
        for value in ("../post.json", "123", "0" * 20):
            self.ns["restore_version"](self.state, value)
        self.assertEqual(self.job, before)


if __name__ == "__main__":
    unittest.main()
