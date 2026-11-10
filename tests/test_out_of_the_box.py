"""A plain install writes transcripts with nothing else set.

No wiki_path, no questions_path, no API key, no transcript or suggestions setting: Hermes loads the
plugin, a turn happens, and today's daily file appears in the plugin's data folder. After the
finish time the day is written once more as final. With a key, the scored pages go under the
message as a Wiki pages: block, also with no setting.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "examples" / "wiki"
MESSAGE = "Tell me how the soil in the garden beds is fed."
_COUNTER = itertools.count()
_SHARED_NAME = "_nyx_wiki_recall_shared_state"
_SUBMODULES = ("transcript", "suggestions", "finalize", "storage", "__init__")


def _load_copy():
    name = f"nwr_out_of_the_box_{next(_COUNTER)}"
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module, name


class _Ctx:
    """What Hermes hands a freshly installed plugin: no settings at all, unless a test adds one."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.hooks = {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_tool(self, **kwargs):
        del kwargs

    def register_memory_provider(self, provider):
        del provider

    def on_unload(self, callback):
        del callback


def _every_candidate_fits(message, items, timeout, **kwargs):
    del message, timeout, kwargs
    return {item["id"]: 0.9 for item in items}


class OutOfTheBoxTest(unittest.TestCase):
    def setUp(self):
        sys.modules.pop(_SHARED_NAME, None)
        self.addCleanup(sys.modules.pop, _SHARED_NAME, None)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "plugin-data" / "nyx-wiki-recall"
        self.data.mkdir(parents=True)
        self.pkg, self.name = _load_copy()
        self.addCleanup(self._unload)
        # The plugin's data folder is the only thing redirected: everything else is as shipped.
        for sub in _SUBMODULES:
            module = self.pkg if sub == "__init__" else sys.modules[f"{self.name}.{sub}"]
            if hasattr(module, "data_dir"):
                patch = mock.patch.object(module, "data_dir", lambda: self.data)
                patch.start()
                self.addCleanup(patch.stop)
        for patch in (mock.patch.object(self.pkg, "_provider_setting", lambda: None),
                      mock.patch.object(self.pkg, "start_finalizer", lambda *a, **k: None)):
            patch.start()
            self.addCleanup(patch.stop)

    def _unload(self):
        for key in [k for k in sys.modules if k == self.name or k.startswith(self.name + ".")]:
            sys.modules.pop(key, None)

    def _sub(self, name):
        return sys.modules[f"{self.name}.{name}"]

    def _key(self, value: str):
        for target in (self.pkg, self._sub("jev_client")):
            if hasattr(target, "api_key"):
                patch = mock.patch.object(target, "api_key", lambda: value)
                patch.start()
                self.addCleanup(patch.stop)
        if hasattr(self.pkg, "_api_key"):
            patch = mock.patch.object(self.pkg, "_api_key", lambda: value)
            patch.start()
            self.addCleanup(patch.stop)

    def _turn(self, ctx: _Ctx, reply: str = "Compost, twice a year."):
        self.pkg.register(ctx)
        ctx.hooks["pre_llm_call"](user_message=MESSAGE, conversation_history=[], session_id="s1",
                                  turn_id="t1", platform="cli")
        ctx.hooks["post_llm_call"](session_id="s1", assistant_response=reply,
                                   conversation_history=[{"role": "user", "content": MESSAGE},
                                                         {"role": "assistant", "content": reply}],
                                   turn_id="t1", platform="cli")
        self.assertTrue(self._sub("suggestions").wait_for_refresh(10.0))

    def _today(self) -> Path:
        transcript = self._sub("transcript")
        return self.data / "transcripts" / transcript.daily_name(transcript.day_at({}, datetime.now()))

    def test_the_shipped_settings_turn_transcripts_and_finishing_on(self):
        questions = self._sub("questions").load(ROOT / "questions.txt")
        self.assertTrue(questions.flag("transcript", False))
        self.assertTrue(questions.flag("finalize", False))
        self.assertEqual(questions.get("transcript.folder"), "")
        self.assertEqual(questions.get("suggestions.folder"), "")
        writer = self._sub("transcript").settings(questions)
        self.assertEqual(Path(writer["folder"]), self.data / "transcripts")
        self.assertEqual(Path(writer["suggestions"]), self.data / "suggestions")

    def test_a_missing_setting_still_means_transcripts_on(self):
        writer = self._sub("transcript").settings(self._sub("questions").parse(""))
        self.assertIsNotNone(writer)
        self.assertEqual(Path(writer["folder"]), self.data / "transcripts")
        self.assertIsNotNone(self._sub("finalize").settings(self._sub("questions").parse("")))

    def test_a_plain_install_with_no_key_and_no_wiki_writes_today(self):
        self._key("")
        self._turn(_Ctx())
        path = self._today()
        self.assertTrue(path.is_file(), list(self.data.rglob("*")))
        text = path.read_text(encoding="utf-8")
        self.assertIn("**Status:** open", text)
        self.assertIn("**Exporter: nyx-wiki-recall**", text)
        self.assertIn(MESSAGE, text)
        self.assertIn("### Assistant (", text)
        self.assertIn("Compost, twice a year.", text)
        self.assertNotIn("Wiki pages:", text)  # no key: not looked at, so no block
        self.assertTrue((self.data / "transcripts" / "transcriptIndex.md").is_file())

    def test_with_a_key_the_scored_pages_go_under_the_message(self):
        self._key("test-key")
        for target in (self.pkg, self._sub("jev_client")):
            for attr in ("jev_ask", "ask"):
                if hasattr(target, attr):
                    patch = mock.patch.object(target, attr, _every_candidate_fits)
                    patch.start()
                    self.addCleanup(patch.stop)
        self._turn(_Ctx({"wiki_path": str(WIKI)}))
        text = self._today().read_text(encoding="utf-8")
        self.assertIn("Wiki pages:\n1. ", text)
        self.assertIn("garden/soil.md", text)
        self.assertTrue(any((self.data / "suggestions").glob("*.jsonl")))

    def test_the_day_is_finished_at_two_with_no_setting(self):
        self._key("")
        self._turn(_Ctx())
        finalize = self._sub("finalize")
        questions = self._sub("questions").load(ROOT / "questions.txt")
        # A state file that already covers the day before, as after a first run yesterday.
        today = self._sub("transcript").day_at({}, datetime.now())  # the transcript day: 02:00 to 02:00
        finalize.save_state(self.data / "finalized.json",
                            {"final_through": (today - timedelta(days=1)).isoformat(), "days": {}})
        later = datetime.combine(today + timedelta(days=1), datetime.min.time()) + timedelta(hours=2, minutes=5)
        done = finalize.tick(questions, ROOT, now=later)
        self.assertIn((today.isoformat(), "final"), done)
        self.assertIn("**Status:** final", self._today().read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
