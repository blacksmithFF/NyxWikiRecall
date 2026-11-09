"""The same posts are eligible in both positions of provider in questions.txt.

A person, another bot, an assistant digest, and webhook posts reach the plugin as the turn's user message.
Both positions run them through recall.skip_reason with the same questions.txt rules, so a post
skipped in one position is skipped in the other, and a post scored in one is scored in the other.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_toggle import ROOT, WIKI, _Ctx, _SHARED_NAME, _every_candidate_fits, _load_copy

POSTS = {
    "person": "[Robin] Tell me how the soil in the garden beds is fed.",
    "bot": "[Helper [bot]] Robin asked how the soil in the garden beds is fed. Do you remember?",
    "digest": "[Digest] People wrote to you.  1) X - someone asked how the soil in the garden beds is fed.",
    "webhook": "[Webhook Poster] Agent, the soil in the garden beds: how is it fed?",
    "heartbeat": "[Webhook Poster] [Heartbeat - recurring instruction, fires every 4h]\nDo anything at all that you wish.",
}
EXTRA_RULE = "skip.line = ^\\[[^\\]]{1,40}\\] \\[Heartbeat\\b\n"


class ParityTest(unittest.TestCase):
    def _run(self, provider: str, message: str) -> dict:
        sys.modules.pop(_SHARED_NAME, None)
        refreshes: list = []
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            notes = folder / "suggestions"
            path = folder / "questions.txt"
            path.write_text(
                (ROOT / "questions.txt").read_text(encoding="utf-8-sig")
                + "\n" + EXTRA_RULE
                + f"provider = {provider}\nsuggestions.folder = {notes.as_posix()}\n",
                encoding="utf-8",
            )
            hook_pkg, hook_name = _load_copy("nwr_parity_hook")
            memory_pkg, memory_name = _load_copy("nwr_parity_memory")
            patches = []
            for pkg, name in ((hook_pkg, hook_name), (memory_pkg, memory_name)):
                patches += [
                    mock.patch.object(sys.modules[f"{name}.suggestions"], "refresh_transcript", refreshes.append),
                    mock.patch.object(pkg, "jev_ask", _every_candidate_fits),
                    mock.patch.object(sys.modules[f"{name}.jev_client"], "ask", _every_candidate_fits),
                    mock.patch.object(sys.modules[f"{name}.jev_client"], "api_key", lambda: "test-key"),
                    mock.patch.object(pkg, "_provider_setting", lambda: None),
                ]
            for patch in patches:
                patch.start()
            try:
                provider_obj = memory_pkg.WikiRecallProvider(questions_path=str(path), wiki_path=str(WIKI))
                provider_obj.initialize("s1")
                ctx = _Ctx(path)
                hook_pkg.register(ctx)
                hook_out = ctx.hooks["pre_llm_call"](user_message=message, conversation_history=[], session_id="s1")
                prefetch_out = provider_obj.prefetch(message, session_id="s1")
                ctx.hooks["post_llm_call"](session_id="s1")
                self.assertTrue(sys.modules[f"{hook_name}.suggestions"].wait_for_refresh(10.0))
                provider_obj.shutdown()
                rows = [json.loads(line) for f in sorted(notes.glob("*.jsonl"))
                        for line in f.read_text(encoding="utf-8").splitlines()]
            finally:
                for patch in patches:
                    patch.stop()
                for key in [k for k in sys.modules if k.split(".")[0] in {hook_name, memory_name}]:
                    sys.modules.pop(key, None)
                sys.modules.pop(_SHARED_NAME, None)
        glimpses = (hook_out or {}).get("context", "") if provider == "no" else prefetch_out
        return {
            "rows": [(row["asked"], bool(row["pages"])) for row in rows],
            "refreshes": len(refreshes),
            "glimpses": bool(glimpses),
        }

    def test_every_sender_gets_the_same_treatment_in_both_positions(self):
        for sender, message in POSTS.items():
            with self.subTest(sender=sender):
                hook_mode = self._run("no", message)
                memory_mode = self._run("yes", message)
                self.assertEqual(hook_mode, memory_mode)
                self.assertEqual(len(hook_mode["rows"]), 1)
                self.assertEqual(hook_mode["refreshes"], 1)

    def test_bot_and_webhook_posts_are_scored_and_the_rule_still_skips(self):
        for sender in ("person", "bot", "digest", "webhook"):
            with self.subTest(sender=sender):
                for provider in ("no", "yes"):
                    result = self._run(provider, POSTS[sender])
                    self.assertEqual(result["rows"], [(True, True)])
                    self.assertTrue(result["glimpses"])
        for provider in ("no", "yes"):
            result = self._run(provider, POSTS["heartbeat"])
            self.assertEqual(result["rows"], [(False, False)])
            self.assertFalse(result["glimpses"])


if __name__ == "__main__":
    unittest.main()