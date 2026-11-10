"""A transcript day ends at finalize.after (02:00 by default), not at midnight."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests  # noqa: F401  (temp HERMES_HOME)
import finalize
import transcript
from questions import parse

ROOT = Path(__file__).resolve().parents[1]
NOV9 = date(2026, 11, 9)
NOV10 = date(2026, 11, 10)


def at(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


class Boundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        patch = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.dir / "home")})
        patch.start()
        self.addCleanup(patch.stop)
        self.use("")

    def use(self, extra: str):
        self.q = parse(
            f"transcript.folder = {self.dir / 'transcripts'}\ntranscript.journal = {self.dir / 'journal'}\n"
            f"suggestions.folder = {self.dir / 'suggestions'}\nfinalize.state = {self.dir / 'finalized.json'}\n"
            + extra)
        self.cfg = transcript.settings(self.q)

    def turn(self, iso: str, text: str, turn: str):
        transcript.record_user(self.cfg, user_message=text, session_id="s", turn_id=turn, platform="discord",
                               ts=at(iso), chat_type="dm")
        transcript.record_reply(self.cfg, assistant_response=f"re: {text}", session_id="s", turn_id=turn,
                                platform="discord", ts=at(iso) + 1)

    def render(self, day: date) -> str:
        return transcript.render_day(day, transcript._journal_rows(self.cfg, day), self.cfg, status="open",
                                     exported_at=datetime(2026, 11, 10, 3, 0)) or ""


class DefaultTest(Boundary):
    def test_default_day_ends_at_two(self):
        self.assertEqual(self.cfg["day_ends"], "02:00")
        shipped = transcript.settings(parse((ROOT / "questions.txt").read_text(encoding="utf-8-sig")))
        self.assertEqual(shipped["day_ends"], "02:00")
        self.assertEqual(transcript.settings(parse(""))["day_ends"], "02:00")

    def test_bad_setting_falls_back_to_two(self):
        self.use("finalize.after = soon\n")
        self.assertEqual(self.cfg["day_ends"], "02:00")

    def test_0159_is_the_day_before_and_0200_is_the_new_day(self):
        self.assertEqual(transcript.day_at(self.cfg, datetime(2026, 11, 10, 1, 59, 59)), NOV9)
        self.assertEqual(transcript.day_at(self.cfg, datetime(2026, 11, 10, 2, 0, 0)), NOV10)
        self.assertEqual(transcript.day_at(self.cfg, datetime(2026, 11, 10, 0, 0, 0)), NOV9)
        self.assertEqual(transcript.day_at(self.cfg, datetime(2026, 11, 9, 23, 59)), NOV9)

    def test_turns_land_in_the_right_file_and_never_twice(self):
        self.turn("2026-11-09T23:30:00", "late evening", "a")
        self.turn("2026-11-10T00:30:00", "after midnight", "b")
        self.turn("2026-11-10T01:59:00", "one fifty-nine", "c")
        self.turn("2026-11-10T02:00:00", "two sharp", "d")
        nine, ten = self.render(NOV9), self.render(NOV10)
        for words in ("late evening", "after midnight", "one fifty-nine"):
            self.assertIn(words, nine)
            self.assertNotIn(words, ten)
        self.assertIn("two sharp", ten)
        self.assertNotIn("two sharp", nine)
        self.assertIn("### User (2026-11-10 00:30:00)", nine)  # real clock time is kept
        self.assertIn("**Messages:** 6", nine)
        self.assertIn("**Messages:** 2", ten)
        # the journal is filed by transcript day
        self.assertTrue((self.dir / "journal" / "2026-11-09.jsonl").is_file())
        rows = [json.loads(l) for l in (self.dir / "journal" / "2026-11-09.jsonl").read_text().splitlines()]
        self.assertEqual(len(rows), 6)

    def test_refresh_today_writes_yesterday_before_two(self):
        self.turn("2026-11-10T01:30:00", "half past one", "a")
        self.assertEqual(transcript.refresh_today(self.cfg, now=datetime(2026, 11, 10, 1, 30, 5)), "wrote")
        files = sorted(p.name for p in (self.dir / "transcripts").glob("*-transcript.md"))
        self.assertEqual(files, ["Nov-09-2026-transcript.md"])

    def test_due_day_matches_the_boundary(self):
        after = time(2, 0)
        self.assertEqual(finalize.last_due_day(datetime(2026, 11, 10, 1, 59), after), date(2026, 11, 8))
        self.assertEqual(finalize.last_due_day(datetime(2026, 11, 10, 2, 0), after), NOV9)
        self.assertEqual(finalize.last_due_day(datetime(2026, 11, 10, 23, 59), after), NOV9)

    def test_the_day_is_sealed_at_two_with_its_after_midnight_turns(self):
        (self.dir / "finalized.json").write_text(json.dumps({"final_through": "2026-11-08", "days": {}}))
        self.turn("2026-11-09T12:00:00", "noon", "a")
        self.turn("2026-11-10T01:45:00", "quarter to two", "b")
        self.assertEqual(finalize.tick(self.q, self.dir, now=datetime(2026, 11, 10, 1, 59)), [])
        self.assertEqual(finalize.tick(self.q, self.dir, now=datetime(2026, 11, 10, 2, 0)), [("2026-11-09", "final")])
        text = (self.dir / "transcripts" / "Nov-09-2026-transcript.md").read_text(encoding="utf-8")
        self.assertIn("**Status:** final", text)
        self.assertIn("noon", text)
        self.assertIn("quarter to two", text)
        # a turn after the seal goes to the next day, not into the final file
        self.turn("2026-11-10T02:10:00", "after the seal", "c")
        transcript.refresh_today(self.cfg, now=datetime(2026, 11, 10, 2, 10, 5))
        self.assertNotIn("after the seal", (self.dir / "transcripts" / "Nov-09-2026-transcript.md").read_text())
        self.assertIn("after the seal", (self.dir / "transcripts" / "Nov-10-2026-transcript.md").read_text())


class CustomAfterTest(Boundary):
    def test_four_am(self):
        self.use("finalize.after = 04:00\n")
        self.assertEqual(self.cfg["day_ends"], "04:00")
        self.assertEqual(transcript.day_at(self.cfg, datetime(2026, 11, 10, 3, 59)), NOV9)
        self.assertEqual(transcript.day_at(self.cfg, datetime(2026, 11, 10, 4, 0)), NOV10)
        fin = finalize.settings(self.q)
        self.assertEqual(fin["after"], time(4, 0))
        self.assertEqual(finalize.last_due_day(datetime(2026, 11, 10, 3, 59), fin["after"]), date(2026, 11, 8))
        self.assertEqual(finalize.last_due_day(datetime(2026, 11, 10, 4, 0), fin["after"]), NOV9)
        self.turn("2026-11-10T03:30:00", "three thirty", "a")
        self.assertIn("three thirty", self.render(NOV9))
        self.assertNotIn("three thirty", self.render(NOV10))

    def test_midnight_keeps_calendar_days(self):
        self.use("finalize.after = 00:00\n")
        self.assertEqual(transcript.day_at(self.cfg, datetime(2026, 11, 10, 0, 0)), NOV10)
        self.assertEqual(transcript.day_at(self.cfg, datetime(2026, 11, 9, 23, 59, 59)), NOV9)
        self.assertEqual(finalize.last_due_day(datetime(2026, 11, 10, 0, 0), time(0, 0)), NOV9)


if __name__ == "__main__":
    unittest.main()
