import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests  # noqa: F401  (temp HERMES_HOME)
from questions import parse
from suggestions import (LEGACY_NAME, append_record, day_file, index_for_day, load_index, message_keys, pages_for,
                         record_turn, suggestions_dir)


class SuggestionTest(unittest.TestCase):
    def test_keys_cover_the_message_the_tail_and_one_tag(self):
        text = "[Robin] The candle was at 3:33."
        keys = message_keys(text)
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys, message_keys("  [Robin]   The candle was at 3:33. \n"))
        wrapped = "older lines\n[New message]\n" + text
        wrapped_keys = message_keys(wrapped)
        self.assertTrue(set(keys) & set(wrapped_keys))

    def test_the_file_keeps_paths_and_scores_and_leaves_out_the_message(self):
        message = "The candle was at 3:33."
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wiki_suggestions.jsonl"
            append_record(path, message, [{"path": "garden/soil.md", "score": 0.91}], asked=True)
            append_record(path, message, [], asked=True)
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn(message, raw)
            index = load_index(path)
            self.assertEqual(pages_for(index, message), [])
            self.assertIsNone(pages_for(index, "a different sentence"))

    def test_latest_line_wins_and_a_scored_gap_is_an_empty_list(self):
        questions = parse("suggestions.folder =\n")
        with tempfile.TemporaryDirectory() as tmp:
            questions.values["suggestions.folder"] = tmp
            now = datetime(2026, 3, 4, 9, 0)
            record_turn(questions, "Where did the candle go?", {"asked": True, "pages": []}, now=now)
            record_turn(questions, "thanks", {"asked": False, "pages": []}, now=now)
            record_turn(
                questions,
                "[Robin] [Heartbeat] Do anything you wish.",
                {"asked": False, "ineligible": True, "pages": [{"path": "garden/soil.md", "score": 0.9}]},
                now=now,
            )
            path = day_file(tmp, date(2026, 3, 4))
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["pages"], [])
            self.assertTrue(rows[0]["asked"])
            self.assertFalse(rows[1]["asked"])
            self.assertEqual(rows[1]["pages"], [])


class PerDayFileTest(unittest.TestCase):
    """Decision 2: one suggestions file per day, and a reader reads only the days it needs."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / "suggestions"
        self.q = parse(f"suggestions.folder = {self.folder}\n")

    def test_each_day_gets_its_own_file(self):
        record_turn(self.q, "first day words", {"asked": True, "pages": []}, now=datetime(2026, 3, 4, 23, 59))
        record_turn(self.q, "second day words", {"asked": True, "pages": []}, now=datetime(2026, 3, 5, 0, 1))
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["2026-03-04.jsonl", "2026-03-05.jsonl"])

    def test_a_day_reads_itself_and_its_neighbours_only(self):
        for day, text in ((date(2026, 3, 1), "old"), (date(2026, 3, 3), "before"), (date(2026, 3, 4), "same"),
                          (date(2026, 3, 5), "after"), (date(2026, 3, 7), "later")):
            append_record(day_file(self.folder, day), text, [], asked=True)
        opened = []
        real = Path.read_text

        def spy(path, *a, **k):
            opened.append(path.name)
            return real(path, *a, **k)

        with mock.patch.object(Path, "read_text", spy):
            index = index_for_day(self.folder, date(2026, 3, 4))
        self.assertEqual(sorted(opened), ["2026-03-03.jsonl", "2026-03-04.jsonl", "2026-03-05.jsonl"])
        for text in ("before", "same", "after"):
            self.assertIsNotNone(pages_for(index, text))
        for text in ("old", "later"):
            self.assertIsNone(pages_for(index, text))

    def test_a_lookup_that_finished_after_midnight_still_counts_for_the_day_before(self):
        append_record(day_file(self.folder, date(2026, 3, 5)), "asked at 23:59:58", [{"path": "a.md", "score": 0.7}],
                      asked=True)
        self.assertEqual(pages_for(index_for_day(self.folder, date(2026, 3, 4)), "asked at 23:59:58"),
                         [{"path": "a.md", "score": 0.7}])

    def test_off_and_the_old_off_setting(self):
        self.assertIsNone(suggestions_dir(parse("suggestions.folder = off\n")))
        self.assertIsNone(suggestions_dir(parse("suggestions.file = off\n")))
        record_turn(parse("suggestions.folder = off\ntranscript = no\n"), "x", {"asked": True, "pages": []})

    def test_an_old_single_file_is_ignored_kept_and_noted_once(self):
        import suggestions as module
        from bridge import shared
        from storage import data_dir

        legacy = data_dir() / LEGACY_NAME
        legacy.write_text(json.dumps({"keys": message_keys("legacy words"), "asked": True, "pages": []}) + "\n",
                          encoding="utf-8")
        self.addCleanup(lambda: legacy.unlink(missing_ok=True))
        state = shared()
        saved = getattr(state, "legacy_noted", False)
        state.legacy_noted = False
        self.addCleanup(setattr, state, "legacy_noted", saved)
        with self.assertLogs("nyx-wiki-recall", "INFO") as logs:
            self.assertIn("no longer read", module.note_legacy_file())
        self.assertEqual(len(logs.output), 1)
        self.assertEqual(module.note_legacy_file(), "")
        self.assertTrue(legacy.is_file(), "the old file is never deleted")
        index = index_for_day(suggestions_dir(parse("")), date.today())
        self.assertIsNone(pages_for(index, "legacy words"))


if __name__ == "__main__":
    unittest.main()
