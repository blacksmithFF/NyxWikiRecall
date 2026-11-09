"""Fixes from the pre-submission code review: each test pins one bug that was fixed."""

from __future__ import annotations

import importlib.util
import itertools
import os
import sys
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests  # noqa: F401  (temp HERMES_HOME)
import finalize
import transcript
import wiki_tree
from provider import WikiRecallProvider
from questions import load, parse

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 3, 4)
_COUNTER = itertools.count()
_SHARED_NAME = "_nyx_wiki_recall_shared_state"


def ts(hour, minute=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute).timestamp()


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "hermes-home"
        patch = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)
        self.q = parse((ROOT / "questions.txt").read_text(encoding="utf-8-sig"))
        self.cfg = transcript.settings(self.q)


class WikiCacheTest(unittest.TestCase):
    def test_one_tree_per_wiki_folder_after_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "topics").mkdir()
            (root / "topics" / "_about.md").write_text("<!-- glimpse -->Topics.<!-- /glimpse -->", encoding="utf-8")
            page = root / "topics" / "soil.md"
            q = parse("")
            for n in range(5):
                page.write_text(f"Soil note {n}.", encoding="utf-8")
                os.utime(page, ns=(n * 10**9 + 10**18, n * 10**9 + 10**18))
                wiki_tree.load_wiki(str(root), q)
            mine = [key for key in wiki_tree._cache if key[0] == str(root.resolve())]
            self.assertEqual(len(mine), 1)
            self.assertIn("Soil note 4.", wiki_tree.load_wiki(str(root), q).folders[0].articles[0].file_text)


class StaleLockTest(Home):
    def test_old_lock_held_by_a_reused_live_pid_is_taken_over(self):
        folder = Path(self.cfg["folder"])
        folder.mkdir(parents=True)
        lock = folder / transcript.LOCK_NAME
        lock.write_text(str(os.getppid()), encoding="utf-8")  # a live process that is not us
        self.assertFalse(transcript._acquire(lock), "a fresh lock of a live process is respected")
        old = time.time() - transcript.LOCK_STALE_SECONDS - 60
        os.utime(lock, (old, old))
        self.assertTrue(transcript._acquire(lock))
        transcript._release(lock)
        self.assertFalse(lock.exists())


class AtomicWriteTest(Home):
    def test_retries_a_briefly_locked_target_and_leaves_no_temp_file(self):
        target = Path(self.tmp.name) / "day.md"
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("in use")
            return real(src, dst)

        with mock.patch.object(transcript.os, "replace", flaky), mock.patch.object(transcript.time, "sleep"):
            transcript._atomic_write(target, "hello\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "hello\n")
        self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_a_failed_write_removes_its_temp_file(self):
        target = Path(self.tmp.name) / "day.md"
        with mock.patch.object(transcript.os, "replace", side_effect=PermissionError("in use")), \
                mock.patch.object(transcript.time, "sleep"):
            with self.assertRaises(PermissionError):
                transcript._atomic_write(target, "hello\n")
        self.assertEqual(list(target.parent.glob(".*.tmp")), [])


class TimeZoneMoveTest(Home):
    def test_a_row_filed_under_the_next_date_still_lands_on_its_day(self):
        # Written while the clock said Mar 5 (another time zone); by this clock it is Mar 4, 23:30.
        transcript.record_user(self.cfg, user_message="Late question about compost.", session_id="s1",
                               turn_id="t1", platform="telegram", ts=ts(23, 30))
        journal = Path(self.cfg["journal"])
        filed = journal / f"{DAY.isoformat()}.jsonl"
        moved = journal / "2026-03-05.jsonl"
        filed.rename(moved)
        self.assertEqual(transcript.write_day(self.cfg, DAY, status="open"), "wrote")
        text = (Path(self.cfg["folder"]) / transcript.daily_name(DAY)).read_text(encoding="utf-8")
        self.assertIn("Late question about compost.", text)
        self.assertEqual(transcript.write_day(self.cfg, date(2026, 3, 5), status="open"), "empty")


class ToolLineTest(unittest.TestCase):
    def test_wiki_read_by_short_name_shows_the_name(self):
        self.assertEqual(transcript.summarize_tool_call("wiki_read", '{"slug": "compost"}'), "[wiki_read: compost]")
        self.assertEqual(transcript.summarize_tool_call("wiki_read", {"path": "topics/compost.md"}),
                         "[wiki_read: topics/compost.md]")


class RedactionTest(unittest.TestCase):
    def test_more_secret_shapes_are_redacted(self):
        samples = [
            "github_pat_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3",
            "hf_" + "abcdefghijklmnopqrstuvwxyz12",
            "AKIA" + "ABCDEFGHIJKLMNOP",
            "123456789:AA" + "abcdefghijklmnopqrstuvwxyz0123456789",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl",
            "M" + "TA2NTQ0NzA4NjQ3NjA0MTIzNA" + ".GaBcDe." + "abcdefghijklmnopqrstuvwxyz0123",
            "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjE=\n-----END OPENSSH PRIVATE KEY-----",
        ]
        for secret in samples:
            with self.subTest(secret=secret[:12]):
                out = transcript.redact(f"before {secret} after")
                self.assertNotIn(secret, out)
                self.assertIn("[redacted]", out)
                self.assertTrue(out.startswith("before ") and out.endswith(" after"))

    def test_ordinary_text_is_left_alone(self):
        text = "Meet at 10:30 by the AKIA café; ask Sam about hf tokens and the Mars rover."
        self.assertEqual(transcript.redact(text), text)


class FinalizeStateTest(unittest.TestCase):
    def test_save_does_not_share_a_temp_name_with_another_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "finalized.json"
            # Another process's half-written temp file under the old shared name.
            (Path(tmp) / "finalized.json.tmp").mkdir()
            finalize.save_state(path, {"final_through": "2026-03-04"})
            self.assertEqual(finalize.through_of(finalize.load_state(path)), DAY)
            self.assertEqual(list(Path(tmp).glob(".*.tmp")), [])


class _Ctx:
    def __init__(self, questions_path: Path):
        self.questions_path = questions_path
        self.hooks = {}
        self.tools = {}

    def get_config(self, key, default=None):
        return {"questions_path": str(self.questions_path)}.get(key, default)

    def register_hook(self, name, callback):
        self.hooks[name] = callback

    def register_tool(self, **kwargs):
        self.tools[kwargs["name"]] = kwargs["handler"]

    def register_memory_provider(self, provider):
        del provider


class UnreadableQuestionsTest(Home):
    def setUp(self):
        super().setUp()
        sys.modules.pop(_SHARED_NAME, None)
        self.addCleanup(sys.modules.pop, _SHARED_NAME, None)
        self.bad = Path(self.tmp.name) / "questions.txt"
        self.bad.write_bytes(b"transcript.user.name = Ren\xe9\n")  # saved as ANSI, not UTF-8

    def _load_plugin(self):
        name = f"nwr_review_copy_{next(_COUNTER)}"
        spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py",
                                                      submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        self.addCleanup(lambda: [sys.modules.pop(k, None) for k in list(sys.modules)
                                 if k == name or k.startswith(name + ".")])
        spec.loader.exec_module(module)
        return module

    def test_plugin_still_loads_and_wiki_read_fails_softly(self):
        pkg = self._load_plugin()
        ctx = _Ctx(self.bad)
        with mock.patch.object(pkg, "start_finalizer"):
            pkg.register(ctx)
        self.assertIn("pre_llm_call", ctx.hooks)
        self.assertIn("post_llm_call", ctx.hooks)
        self.assertIsInstance(ctx.tools["wiki_read"]({"slug": "soil"}), str)
        self.assertIsNone(ctx.hooks["pre_llm_call"](user_message="hello there friend", session_id="s1",
                                                      platform="cli"))

    def test_provider_reports_unavailable_instead_of_raising(self):
        provider = WikiRecallProvider(questions_path=str(self.bad))
        self.assertFalse(provider.is_available())


if __name__ == "__main__":
    unittest.main()
