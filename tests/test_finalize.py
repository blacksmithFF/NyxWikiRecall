"""The plugin finishes each day after midnight by itself, once, with catch-up for missed days."""

import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tests  # noqa: F401  (temp HERMES_HOME)
import finalize
import transcript
from questions import parse

def _ts(day: date, hour: int = 10) -> float:
    return datetime(day.year, day.month, day.day, hour).timestamp()


class FinalizeTest(unittest.TestCase):
    """The built-in writer is the only way a day is finished (transcript.script was removed)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "finalized.json"
        self.folder = self.dir / "transcripts"
        self.questions = parse(
            f"transcript.folder = {self.folder}\ntranscript.journal = {self.dir / 'journal'}\n"
            f"suggestions.folder = {self.dir / 'suggestions'}\nfinalize.state = {self.state}\n"
        )
        self.writer = transcript.settings(self.questions)

    def tearDown(self):
        self.tmp.cleanup()

    def say(self, *days: str):
        for iso in days:
            day = date.fromisoformat(iso)
            transcript.record_user(self.writer, user_message=f"Hello on {iso}", session_id="s", turn_id=iso,
                                   platform="cli", ts=_ts(day))

    def written(self):
        return sorted(p.name for p in self.folder.glob("*-transcript.md")) if self.folder.is_dir() else []

    def seed(self, through):
        self.state.write_text(json.dumps({"final_through": through, "days": {}}))

    def tick(self, when):
        return finalize.tick(self.questions, self.dir, now=datetime.fromisoformat(when))

    def test_waits_for_grace_then_finishes_once(self):
        self.say("2026-11-09")
        self.seed("2026-11-08")
        self.questions.values["finalize.after"] = "02:05"
        self.assertEqual(self.tick("2026-11-10T00:30"), [])
        self.assertEqual(self.tick("2026-11-10T02:04"), [])
        self.assertEqual(self.tick("2026-11-10T02:05"), [("2026-11-09", "final")])
        self.assertEqual(self.tick("2026-11-10T02:15"), [])
        self.assertEqual(self.tick("2026-11-10T23:59"), [])
        self.assertEqual(self.written(), ["Nov-09-2026-transcript.md"])
        self.assertIn("**Status:** final", (self.folder / "Nov-09-2026-transcript.md").read_text(encoding="utf-8"))
        data = json.loads(self.state.read_text())
        self.assertEqual(data["final_through"], "2026-11-09")
        self.assertEqual(data["days"]["2026-11-09"]["result"], "final")

    def test_catch_up_after_downtime_in_order(self):
        self.say("2026-11-07", "2026-11-09")
        self.seed("2026-11-06")
        got = self.tick("2026-11-10T09:00")
        self.assertEqual(got, [("2026-11-07", "final"), ("2026-11-08", "empty"), ("2026-11-09", "final")])
        self.assertEqual(self.tick("2026-11-10T09:10"), [])

    def test_floor_protects_earlier_days(self):
        self.say("2026-11-08", "2026-11-09")
        self.questions.values["finalize.from"] = "2026-11-09"
        self.seed("2026-11-01")
        self.assertEqual(self.tick("2026-11-10T09:00"), [("2026-11-09", "final")])
        self.assertEqual(self.written(), ["Nov-09-2026-transcript.md"])

    def test_first_run_seeds_and_only_finishes_new_days(self):
        self.say("2026-11-08", "2026-11-09")
        self.questions.values["finalize.after"] = "02:05"
        self.assertEqual(self.tick("2026-11-10T01:00"), [])
        self.assertEqual(json.loads(self.state.read_text())["final_through"], "2026-11-08")
        self.assertEqual(self.tick("2026-11-10T02:10"), [("2026-11-09", "final")])
        self.assertEqual(self.written(), ["Nov-09-2026-transcript.md"])

    def test_first_run_after_grace_leaves_yesterday_alone(self):
        self.say("2026-11-09")
        self.assertEqual(self.tick("2026-11-10T09:00"), [])
        self.assertEqual(self.written(), [])

    def test_busy_is_retried_not_marked(self):
        self.say("2026-11-09")
        self.seed("2026-11-08")
        with mock.patch.object(finalize.builtin, "write_day", return_value="busy"):
            self.assertEqual(self.tick("2026-11-10T03:00"), [("2026-11-09", "busy")])
        self.assertEqual(json.loads(self.state.read_text())["final_through"], "2026-11-08")
        self.assertEqual(self.tick("2026-11-10T03:10"), [("2026-11-09", "final")])

    def test_foreign_file_is_kept(self):
        self.say("2026-11-09")
        self.seed("2026-11-08")
        self.folder.mkdir(parents=True)
        (self.folder / "Nov-09-2026-transcript.md").write_text("hand written\n")
        self.assertEqual(self.tick("2026-11-10T03:00"), [("2026-11-09", "foreign")])
        self.assertEqual((self.folder / "Nov-09-2026-transcript.md").read_text(), "hand written\n")

    def test_empty_day_is_marked(self):
        self.seed("2026-11-08")
        self.assertEqual(self.tick("2026-11-10T03:00"), [("2026-11-09", "empty")])
        self.assertEqual(self.tick("2026-11-10T03:10"), [])

    def test_catchup_limit(self):
        self.questions.values["finalize.catchup.days"] = "2"
        self.seed("2026-10-01")
        self.assertEqual([d for d, _ in self.tick("2026-11-10T03:00")], ["2026-11-08", "2026-11-09"])

    def test_off_does_nothing(self):
        self.say("2026-11-09")
        self.seed("2026-11-08")
        self.questions.values["finalize"] = "no"
        self.assertEqual(self.tick("2026-11-10T03:00"), [])
        self.questions.values["finalize"] = "yes"
        self.questions.values["transcript"] = "no"
        self.assertEqual(self.tick("2026-11-10T03:00"), [])
        self.assertEqual(self.written(), [])
        self.questions.values["transcript"] = "yes"
        self.assertEqual(self.tick("2026-11-10T03:00"), [("2026-11-09", "final")])

    def test_a_leftover_transcript_script_setting_is_never_run(self):
        ran = self.dir / "ran.txt"
        script = self.dir / "my_exporter.py"
        script.write_text(f"open(r'{ran}', 'w').write('ran')\n", encoding="utf-8")
        self.questions.values["transcript.script"] = str(script)
        self.say("2026-11-09")
        self.seed("2026-11-08")
        self.assertEqual(self.tick("2026-11-10T03:00"), [("2026-11-09", "final")])
        self.assertFalse(ran.exists())
        self.assertEqual(self.written(), ["Nov-09-2026-transcript.md"])

    def test_no_studio_exporter_names_left_in_the_code(self):
        root = Path(__file__).resolve().parents[1]
        source = "".join(p.read_text(encoding="utf-8") for p in root.glob("*.py"))
        for name in ("run_one_date", "_export_days", "DEFAULT_DB", "DEFAULT_TRANSCRIPTS", "DEFAULT_LOCK",
                     "_sync_script_state", "_load_script", "spec_from_file_location"):
            self.assertNotIn(name, source)
        shipped = (root / "questions.txt").read_text(encoding="utf-8")
        self.assertNotIn("transcript.script", shipped)

    def test_next_run(self):
        self.seed("2026-11-08")
        nxt = finalize.next_run(self.questions, self.dir, now=datetime(2026, 11, 9, 11, 30))
        self.assertEqual(nxt, datetime(2026, 11, 10, 2, 0))

    def test_default_after_is_two_am(self):
        self.say("2026-11-09")
        self.seed("2026-11-08")
        self.assertEqual(finalize.DEFAULT_AFTER.isoformat(timespec="minutes"), "02:00")
        self.assertEqual(self.tick("2026-11-10T00:30"), [])
        self.assertEqual(self.tick("2026-11-10T01:59"), [])
        self.assertEqual(self.tick("2026-11-10T02:00"), [("2026-11-09", "final")])

    def test_shipped_questions_say_two_am(self):
        from questions import load
        shipped = load(Path(__file__).resolve().parents[1] / "questions.txt")
        self.assertEqual(shipped.get("finalize.after"), "02:00")

    def test_reload_keeps_one_thread(self):
        from bridge import shared
        state = shared()
        saved = getattr(state, "finalize_worker", None), getattr(state, "finalize_once", None)
        try:
            class Alive:
                def is_alive(self):
                    return True
            state.finalize_worker = Alive()
            finalize.start(lambda: None, self.dir)
            self.assertIsInstance(state.finalize_worker, Alive)
            self.assertTrue(callable(state.finalize_once))
        finally:
            state.finalize_worker, state.finalize_once = saved


if __name__ == "__main__":
    unittest.main()