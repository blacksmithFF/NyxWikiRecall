"""Both positions of provider in questions.txt note each turn once and refresh the transcript once.

provider = no: the pre_llm_call hook looks the turn up, notes it, and returns the glimpses.
provider = yes: the hook looks the turn up and notes it, the memory provider hands the glimpses
to the agent. The provider looks the turn up itself only when the hook left nothing for this turn.

Hermes imports the plugin twice under two module names (the hook load and the memory-provider
load), so these tests load two copies the same way.

The note is written before Hermes saves the message, so the transcript refresh waits for
post_llm_call, or, with no post-turn hook, runs a few seconds later in the background.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "examples" / "wiki"
MESSAGE = "Tell me how the soil in the garden beds is fed."
_COUNTER = itertools.count()
_SHARED_NAME = "_nyx_wiki_recall_shared_state"


def _load_copy(prefix: str):
    name = f"{prefix}_{next(_COUNTER)}"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, name


def _every_candidate_fits(message, items, timeout, **kwargs):
    del message, timeout, kwargs
    return {item["id"]: 0.9 for item in items}


class _Ctx:
    def __init__(self, questions_path: Path):
        self.questions_path = questions_path
        self.hooks = {}
        self.unload = []

    def get_config(self, key, default=None):
        return {"wiki_path": str(WIKI), "questions_path": str(self.questions_path)}.get(key, default)

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_tool(self, **kwargs):
        del kwargs

    def register_memory_provider(self, provider):
        del provider

    def on_unload(self, callback):
        self.unload.append(callback)


class ToggleTest(unittest.TestCase):
    def setUp(self):
        sys.modules.pop(_SHARED_NAME, None)
        self.addCleanup(sys.modules.pop, _SHARED_NAME, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.notes = self.dir / "suggestions"
        self.refreshes = []
        self.hook_copy = self._copy("nwr_hook_copy")
        self.memory_copy = self._copy("nwr_memory_copy")

    def _copy(self, prefix: str):
        pkg, name = _load_copy(prefix)
        self.addCleanup(self._unload, name)
        patches = [
            mock.patch.object(sys.modules[f"{name}.suggestions"], "refresh_transcript", self.refreshes.append),
            mock.patch.object(sys.modules[f"{name}.suggestions"], "_LATE_SECONDS", 0.2),
            mock.patch.object(pkg, "jev_ask", _every_candidate_fits),
            mock.patch.object(sys.modules[f"{name}.jev_client"], "ask", _every_candidate_fits),
            mock.patch.object(sys.modules[f"{name}.jev_client"], "api_key", lambda: "test-key"),
            # Never read a real Hermes config.
            mock.patch.object(pkg, "_provider_setting", lambda: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return pkg

    @staticmethod
    def _unload(name: str) -> None:
        for key in [key for key in sys.modules if key == name or key.startswith(name + ".")]:
            sys.modules.pop(key, None)

    def _wait(self):
        suggestions = sys.modules[self.hook_copy.__name__ + ".suggestions"]
        self.assertTrue(suggestions.wait_for_refresh(10.0))

    def _questions(self, provider: str) -> Path:
        text = (ROOT / "questions.txt").read_text(encoding="utf-8-sig")
        text += (
            f"\nprovider = {provider}\n"
            f"suggestions.folder = {self.notes.as_posix()}\n"
        )
        path = self.dir / f"questions-{provider}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def _hooks(self, questions_path: Path):
        ctx = _Ctx(questions_path)
        self.hook_copy.register(ctx)
        return ctx.hooks["pre_llm_call"], ctx.hooks["post_llm_call"]

    def _provider(self, questions_path: Path, session: str = "s1"):
        provider = self.memory_copy.WikiRecallProvider(questions_path=str(questions_path), wiki_path=str(WIKI))
        provider.initialize(session)
        self.addCleanup(provider.shutdown)
        return provider

    def _rows(self) -> list[dict]:
        return [json.loads(line) for f in sorted(self.notes.glob("*.jsonl"))
                for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]

    def assert_noted_once(self):
        self._wait()
        rows = self._rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertTrue(rows[0]["asked"])
        self.assertTrue(rows[0]["pages"])
        # One refresh of today's file, by the built-in writer (its settings are the target).
        self.assertEqual(len(self.refreshes), 1, self.refreshes)
        self.assertIsInstance(self.refreshes[0], dict)

    def test_provider_no_the_hook_notes_the_turn_and_refreshes_after_the_reply(self):
        pre, post = self._hooks(self._questions("no"))
        result = pre(user_message=MESSAGE, conversation_history=[], session_id="s1")
        self.assertTrue(result and "soil" in result["context"].lower())
        self._wait()
        self.assertEqual(self.refreshes, [], "refresh must wait until Hermes saved the turn")
        self.assertEqual(len(self._rows()), 1)
        post(session_id="s1", assistant_response="reply")
        self.assert_noted_once()

    def test_provider_no_an_installed_provider_stays_quiet(self):
        path = self._questions("no")
        provider = self._provider(path)
        pre, post = self._hooks(path)
        self.assertTrue(pre(user_message=MESSAGE, conversation_history=[], session_id="s1"))
        self.assertEqual(provider.prefetch(MESSAGE, session_id="s1"), "")
        post(session_id="s1")
        self.assert_noted_once()

    def test_provider_yes_hook_notes_and_the_other_copy_attaches(self):
        path = self._questions("yes")
        provider = self._provider(path)
        pre, post = self._hooks(path)
        self.assertIsNone(pre(user_message=MESSAGE, conversation_history=[], session_id="s1"))
        self.assertIn("soil", provider.prefetch(MESSAGE, session_id="s1").lower())
        post(session_id="s1")
        self.assert_noted_once()

    def test_provider_yes_a_tidied_query_is_not_looked_up_twice(self):
        path = self._questions("yes")
        provider = self._provider(path)
        pre, post = self._hooks(path)
        pre(user_message="[Robin] " + MESSAGE + "\n<skill text>", conversation_history=[], session_id="s1")
        self.assertIn("soil", provider.prefetch(MESSAGE, session_id="s1").lower())
        post(session_id="s1")
        self.assert_noted_once()

    def test_provider_yes_without_the_hook_the_provider_notes_and_refreshes_later(self):
        provider = self._provider(self._questions("yes"))
        started = time.monotonic()
        self.assertIn("soil", provider.prefetch(MESSAGE, session_id="s1").lower())
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(self.refreshes, [], "the reply does not wait for the refresh")
        self.assert_noted_once()

    def test_provider_yes_another_agent_stopping_keeps_the_hand_off(self):
        path = self._questions("yes")
        provider = self._provider(path)
        other = self.memory_copy.WikiRecallProvider(questions_path=str(path), wiki_path=str(WIKI))
        other.initialize("worker")
        other.shutdown()
        pre, post = self._hooks(path)
        self.assertIsNone(pre(user_message=MESSAGE, conversation_history=[], session_id="s1"))
        self.assertIn("soil", provider.prefetch(MESSAGE, session_id="s1").lower())
        post(session_id="s1")
        self.assert_noted_once()

    def test_with_transcripts_off_a_turn_without_a_note_does_not_refresh(self):
        path = self._questions("no")
        path.write_text(path.read_text(encoding="utf-8") + "transcript = no\n", encoding="utf-8")
        _pre, post = self._hooks(path)
        post(session_id="s1")
        self._wait()
        self.assertEqual(self.refreshes, [])

    def test_with_transcripts_on_the_reply_refreshes_today_once(self):
        _pre, post = self._hooks(self._questions("no"))
        post(session_id="s1", assistant_response="reply")
        self._wait()
        self.assertEqual(len(self.refreshes), 1)

    def test_an_old_note_or_another_session_is_not_taken(self):
        bridge = sys.modules[self.hook_copy.__name__ + ".bridge"]
        bridge.store("s1", "an earlier message", "old text", 1)
        self.assertIsNone(bridge.take("s2", "an earlier message"))
        bridge.shared().slot["at"] = time.monotonic() - 3600
        self.assertIsNone(bridge.take("s1", "a new message"))
        self.assertEqual(bridge.take("s1", "an earlier message"), ("old text", 1))


if __name__ == "__main__":
    unittest.main()