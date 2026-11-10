import sqlite3
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import provider as provider_module
from bridge import mark_ready, ready, store, take
from provider import WikiRecallProvider, recent_messages
from questions import parse


class BridgeTest(unittest.TestCase):
    def test_the_provider_reads_the_hook_result_once(self):
        mark_ready(True)
        self.assertTrue(ready())
        store("session-1", "Where is the soil?", "Soil is fed.", 2)
        self.assertEqual(take("session-1", "Where is the soil?"), ("Soil is fed.", 2))
        self.assertIsNone(take("session-1", "Where is the soil?"))
        mark_ready(False)
        self.assertFalse(ready())


class RecentMessageTest(unittest.TestCase):
    def test_saved_messages_keep_the_glimpse_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, "
                "content TEXT, api_content TEXT)"
            )
            connection.execute(
                "INSERT INTO messages (session_id, role, content, api_content) VALUES (?, ?, ?, ?)",
                ("s1", "user", "the path only", "Soil is fed with compost in spring."),
            )
            connection.commit()
            connection.close()
            history = recent_messages(db_path, "s1")
            self.assertEqual(history[0]["content"], "the path only")
            self.assertIn("compost", history[0]["api_content"])
            self.assertEqual(recent_messages(db_path, "missing"), [])


class ProviderTest(unittest.TestCase):
    def setUp(self):
        # Outside Hermes there is no agent.memory_provider.RecallStatus; stand one in.
        if provider_module.RecallStatus is None:
            patch = mock.patch.object(provider_module, "RecallStatus",
                                      namedtuple("RecallStatus", "provider_label count"))
            patch.start()
            self.addCleanup(patch.stop)

    def test_flag_off_adds_nothing_and_paints_no_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "questions.txt"
            path.write_text("provider = no\n", encoding="utf-8")
            provider = WikiRecallProvider(questions_path=str(path))
            self.assertFalse(provider.is_available())
            provider.initialize("s1", hermes_home=tmp)
            self.assertFalse(ready())
            self.assertEqual(provider.prefetch("Where is the soil?", session_id="s1"), "")
            self.assertIsNone(provider.recall_status())

    def test_a_cached_lookup_sets_the_memory_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "questions.txt"
            path.write_text("provider = yes\n", encoding="utf-8")
            provider = WikiRecallProvider(questions_path=str(path))
            self.assertTrue(provider.is_available())
            provider.initialize("s1", hermes_home=tmp)
            self.addCleanup(provider.shutdown)
            self.assertTrue(ready())
            store("s1", "Where is the soil?", "Soil is fed.", 4)
            self.assertEqual(provider.prefetch("Where is the soil?", session_id="s1"), "Soil is fed.")
            status = provider.recall_status()
            self.assertEqual(status.provider_label, "NyxWikiRecall")
            self.assertEqual(status.count, 4)
            provider.shutdown()
            self.assertFalse(ready())

    def test_an_empty_lookup_paints_no_line(self):
        questions = parse("provider = yes\n")
        del questions
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "questions.txt"
            path.write_text("provider = yes\n", encoding="utf-8")
            provider = WikiRecallProvider(questions_path=str(path))
            provider.initialize("s1", hermes_home=tmp)
            store("s1", "thanks", "", None)
            self.assertEqual(provider.prefetch("thanks", session_id="s1"), "")
            self.assertIsNone(provider.recall_status())
            provider.shutdown()


class HookOffInstallTest(unittest.TestCase):
    """`hermes plugins install --enable` (Hermes 0.22.5) sets memory.provider to this plugin and leaves
    plugins.enabled without it, so the hook never loads. Found in the live test on 2026-11-09."""

    def _provider(self, tmp, flag):
        path = Path(tmp) / "questions.txt"
        path.write_text(f"provider = {flag}\n", encoding="utf-8")
        return WikiRecallProvider(questions_path=str(path))

    def test_the_reason_says_the_hook_is_off_and_how_to_turn_it_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = self._provider(tmp, "no")
            config = {"memory": {"provider": "nyx-wiki-recall"}, "plugins": {"entries": {}}}
            with mock.patch.object(provider_module, "_hermes_config", return_value=config):
                reason = provider.unavailable_reason()
            self.assertIn("hook is not enabled", reason)
            self.assertIn("hermes plugins enable nyx-wiki-recall", reason)
            self.assertNotIn("come from the pre_llm_call hook", reason)

    def test_the_reason_is_unchanged_when_the_hook_is_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = self._provider(tmp, "no")
            config = {"plugins": {"enabled": ["nyx-wiki-recall"]}}
            with mock.patch.object(provider_module, "_hermes_config", return_value=config):
                self.assertIn("come from the pre_llm_call hook", provider.unavailable_reason())

    def test_an_unreadable_config_keeps_the_old_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = self._provider(tmp, "no")
            with mock.patch.object(provider_module, "_hermes_config", return_value=None):
                self.assertIn("come from the pre_llm_call hook", provider.unavailable_reason())

    def test_provider_on_with_the_hook_off_warns_once_that_transcripts_are_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = self._provider(tmp, "yes")
            config = {"memory": {"provider": "nyx-wiki-recall"}}
            with mock.patch.object(provider_module, "_hermes_config", return_value=config), \
                    mock.patch.object(provider_module, "_hook_off_warned", False), \
                    self.assertLogs("nyx-wiki-recall", level="WARNING") as logs:
                provider.initialize("s1", hermes_home=tmp)
                provider.initialize("s2", hermes_home=tmp)
                provider.shutdown()
            warnings = [line for line in logs.output if "no transcripts" in line]
            self.assertEqual(len(warnings), 1)
            self.assertIn("hermes plugins enable nyx-wiki-recall", warnings[0])
