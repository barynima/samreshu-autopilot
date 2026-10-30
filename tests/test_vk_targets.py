import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autopilot import vposter


class VKTargetsTests(unittest.TestCase):
    def test_vk_target_ignores_other_configured_networks(self):
        result = {"response": {"vk": [{"id": "123", "name": "Сам решу", "type_social": "vk"}],
                               "tg": [{"id": "999", "name": "Other channel", "type_social": "tg"}]}}
        with patch.dict(os.environ, {"VPOSTER_TARGETS": "tg", "VPOSTER_TOKEN": "test"}), \
             patch.object(vposter.requests, "get", return_value=Mock()), patch.object(vposter, "_check", return_value=result):
            self.assertEqual(vposter.vk_target(), ("vk", "123", "Сам решу"))

    def test_vk_publish_sends_exact_reviewed_caption_to_only_one_group(self):
        with patch.dict(os.environ, {"VPOSTER_TOKEN": "test"}), \
             patch.object(vposter, "targets", side_effect=AssertionError("must use approved group")), \
             patch.object(vposter, "_upload", return_value=[{"type": "video", "id": "media"}]), \
             patch.object(vposter.requests, "post", return_value=Mock()) as post, \
             patch.object(vposter, "_check", return_value={"response": {"id": "post"}}):
            record = vposter.publish_vk("Exact reviewed text", ("vk", "123", "Сам решу"), video=Path("test.mp4"))
        import json
        body = json.loads(post.call_args.kwargs["data"])
        self.assertEqual(body["type_social"], "vk")
        self.assertEqual(body["id_group"], "123")
        self.assertEqual(body["message"], "Exact reviewed text")
        self.assertEqual(record["status"], "queued")

    def test_vk_button_cannot_publish_to_telegram(self):
        with patch.object(vposter.requests, "post") as post:
            with self.assertRaises(ValueError):
                vposter.publish_vk("Caption", ("tg", "999", "Other channel"))
            post.assert_not_called()
