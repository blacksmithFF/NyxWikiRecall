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
