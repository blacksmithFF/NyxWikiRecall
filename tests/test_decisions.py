"""Regression tests for the six code-review decisions (Nov 9, 2026).

1. transcript.script is gone: the built-in writer is the only transcript path (see test_finalize).
2. Working files are one per day, and keep.days removes old finished days' working files only.
3. A leading [Name] is the speaker only in a shared chat.
4. get_session_env stays, with a comment citing Hermes's plugin guide.
5. With no OpenRouter key: no block, no Wiki pages: row, one warning at load.
6. No wiki lookups on scheduled (cron) turns unless lookups.on_cron = yes.
"""

from __future__ import annotations

import json
import logging
import os
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
from suggestions import append_record, day_file
from tests.test_toggle import WIKI, _SHARED_NAME, _Ctx, _load_copy

ROOT = Path(__file__).resolve().parents[1]
MESSAGE = "Tell me how the soil in the garden beds is fed."


def _ts(day: date, hour: int = 10) -> float:
    return datetime(day.year, day.month, day.day, hour).timestamp()


def _fake_env(**values):
    def get_session_env(name, default=""):
        return values.get(name, default)
    return lambda: get_session_env


# --- 2. per-day working files and keep.days -------------------------------------------------------

class KeepDaysTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.state = self.dir / "finalized.json"
        self.folder = self.dir / "transcripts"
        self.journal = self.dir / "journal"
        self.notes = self.dir / "suggestions"
        self.q = parse(
            f"transcript.folder = {self.folder}\ntranscript.journal = {self.journal}\n"
            f"suggestions.folder = {self.notes}\nfinalize.state = {self.state}\n"
        )
        self.writer = transcript.settings(self.q)

    def say(self, iso: str) -> None:
        day = date.fromisoformat(iso)
        transcript.record_user(self.writer, user_message=f"Hello on {iso}", session_id="s", turn_id=iso,
                               platform="cli", ts=_ts(day))
        append_record(day_file(self.notes, day), f"Hello on {iso}", [], asked=True)

    def finish(self, *isos: str, through: str | None = None) -> None:
        """Write each day as final with the built-in writer and note it as finalize does."""
        data = {"final_through": through or isos[-1], "days": {}}
        for iso in isos:
            result = finalize.finalize_builtin(self.writer, date.fromisoformat(iso), datetime(2026, 11, 9, 3))
            data["days"][iso] = {"result": result, "at": "x"}
        self.state.write_text(json.dumps(data), encoding="utf-8")

    def names(self, folder: Path) -> list[str]:
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []

    def test_journal_and_suggestions_are_one_file_per_day(self):
        self.say("2026-11-01")
        self.say("2026-11-02")
        self.assertEqual(self.names(self.journal), ["2026-11-01.jsonl", "2026-11-02.jsonl"])
        self.assertEqual(self.names(self.notes), ["2026-11-01.jsonl", "2026-11-02.jsonl"])

    def test_default_keeps_everything(self):
        for iso in ("2026-10-01", "2026-10-02", "2026-10-03"):
            self.say(iso)
        self.finish("2026-10-01", "2026-10-02", "2026-10-03")
        self.assertEqual(finalize.keep_days(self.q), 0)
        finalize.tick(self.q, self.dir, now=datetime(2026, 11, 9, 3))
        self.assertEqual(finalize.clean_up(finalize.settings(self.q), finalize.load_state(self.state),
                                           datetime(2026, 11, 9, 3)), [])
        self.assertEqual(len(self.names(self.journal)), 3)
        self.assertEqual(len(self.names(self.notes)), 3)

    def test_old_finished_days_lose_working_files_only(self):
        for iso in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-11-07"):
            self.say(iso)
        self.finish("2026-10-01", "2026-10-02", "2026-10-03", "2026-11-07")
        (self.journal / "notes.txt").write_text("not a day file", encoding="utf-8")
        (self.journal / "2026-10-01.jsonl.bak").write_text("not a day file", encoding="utf-8")
        legacy = self.dir / "wiki_suggestions.jsonl"
        legacy.write_text("{}\n", encoding="utf-8")
        transcripts_before = self.names(self.folder)
        self.q.values["keep.days"] = "30"
        cfg = finalize.settings(self.q)
        with self.assertLogs("nyx-wiki-recall", "INFO") as logs:
            removed = finalize.clean_up(cfg, finalize.load_state(self.state), datetime(2026, 11, 9, 3))
        self.assertEqual(sorted(f"{p.parent.name}/{p.name}" for p in removed), [
            "journal/2026-10-01.jsonl", "journal/2026-10-02.jsonl", "journal/2026-10-03.jsonl",
            "suggestions/2026-10-01.jsonl", "suggestions/2026-10-02.jsonl", "suggestions/2026-10-03.jsonl"])
        self.assertTrue(any("removed 6 old working file(s)" in line for line in logs.output))
        self.assertEqual(self.names(self.journal), ["2026-10-01.jsonl.bak", "2026-11-07.jsonl", "notes.txt"])
        self.assertEqual(self.names(self.notes), ["2026-11-07.jsonl"])
        # The rendered transcripts, the index, the state file and the old single file stay.
        self.assertEqual(self.names(self.folder), transcripts_before)
        self.assertIn("Oct-01-2026-transcript.md", transcripts_before)
        self.assertIn("transcriptIndex.md", transcripts_before)
        self.assertTrue(self.state.is_file())
        self.assertTrue(legacy.is_file())

    def test_a_day_not_finished_is_never_removed(self):
        for iso in ("2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"):
            self.say(iso)
        # 10-01 final, 10-02 never finalized (skipped), 10-03 foreign, 10-04 final but its file was
        # replaced by hand, 10-05 is final_through (the next day still reads it as a neighbour).
        self.finish("2026-10-01", "2026-10-04", "2026-10-05")
        data = json.loads(self.state.read_text())
        data["days"]["2026-10-03"] = {"result": "foreign", "at": "x"}
        self.state.write_text(json.dumps(data), encoding="utf-8")
        (self.folder / transcript.daily_name(date(2026, 10, 4))).write_text("by hand\n", encoding="utf-8")
        self.q.values["keep.days"] = "7"
        cfg = finalize.settings(self.q)
        removed = finalize.clean_up(cfg, finalize.load_state(self.state), datetime(2026, 11, 9, 3))
        self.assertEqual(sorted(p.name for p in removed), ["2026-10-01.jsonl", "2026-10-01.jsonl"])
        self.assertEqual(self.names(self.journal),
                         ["2026-10-02.jsonl", "2026-10-03.jsonl", "2026-10-04.jsonl", "2026-10-05.jsonl"])

    def test_recent_days_are_kept(self):
        for iso in ("2026-11-01", "2026-11-02", "2026-11-03"):
            self.say(iso)
        self.finish("2026-11-01", "2026-11-02", "2026-11-03", through="2026-11-08")
        self.q.values["keep.days"] = "7"
        cfg = finalize.settings(self.q)
        removed = finalize.clean_up(cfg, finalize.load_state(self.state), datetime(2026, 11, 9, 3))
        # 11-01 is 8 days old (removed); 11-02 is exactly 7 (kept).
        self.assertEqual(sorted(p.name for p in removed), ["2026-11-01.jsonl", "2026-11-01.jsonl"])

    def test_runs_after_the_nightly_finish(self):
        for iso in ("2026-10-01", "2026-11-08"):
            self.say(iso)
        self.finish("2026-10-01", through="2026-11-07")
        self.q.values["keep.days"] = "30"
        self.assertEqual(finalize.tick(self.q, self.dir, now=datetime(2026, 11, 9, 2, 0)), [("2026-11-08", "final")])
        self.assertEqual(self.names(self.journal), ["2026-11-08.jsonl"])
        self.assertTrue((self.folder / "Oct-01-2026-transcript.md").is_file())
        self.assertTrue((self.folder / "Nov-08-2026-transcript.md").is_file())

    def test_another_process_cleaning_is_respected(self):
        self.say("2026-10-01")
        self.finish("2026-10-01", "2026-10-02", through="2026-10-02")
        self.q.values["keep.days"] = "7"
        cfg = finalize.settings(self.q)
        lock = self.state.parent / finalize.CLEANUP_LOCK
        lock.write_text("4242", encoding="utf-8")
        with mock.patch.object(transcript, "_pid_alive", return_value=True):
            self.assertEqual(finalize.clean_up(cfg, finalize.load_state(self.state), datetime(2026, 11, 9, 3)), [])
        self.assertEqual(self.names(self.journal), ["2026-10-01.jsonl"])
        lock.unlink()
        # A file the other process removed a moment ago is skipped, not an error.
        real_unlink = Path.unlink

        def gone(path, *a, **k):
            if path.suffix == ".jsonl":
                real_unlink(path)
                raise FileNotFoundError(path)
            return real_unlink(path, *a, **k)

        with mock.patch.object(Path, "unlink", gone):
            self.assertEqual(finalize.clean_up(cfg, finalize.load_state(self.state), datetime(2026, 11, 9, 3)), [])
        self.assertEqual(self.names(self.journal), [])
        self.assertFalse(lock.exists(), "the lock is released")

    def test_a_bad_value_keeps_everything(self):
        self.q.values["keep.days"] = "a month"
        self.assertEqual(finalize.keep_days(self.q), 0)
        self.q.values["keep.days"] = "-3"
        self.assertEqual(finalize.keep_days(self.q), 0)

    def test_the_transcript_folder_is_never_cleaned_even_if_the_journal_points_there(self):
        self.q.values["transcript.journal"] = str(self.folder)
        self.q.values["keep.days"] = "1"
        cfg = finalize.settings(self.q)
        self.assertNotIn(self.folder.resolve(), [p.resolve() for p in finalize.working_folders(cfg)])


# --- 3. [Name] only in shared chats ----------------------------------------------------------------

class NameTagTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.q = parse(f"transcript.journal = {self.tmp.name}/j\ntranscript.folder = {self.tmp.name}/t\n")
        self.cfg = transcript.settings(self.q)
        self.day = date(2026, 3, 4)

    def render(self):
        rows = transcript.read_journal(self.cfg, self.day)
        return transcript.render_day(self.day, rows, self.cfg, status="open", exported_at=datetime(2026, 3, 4, 23))

    def user(self, text, hour, **kw):
        transcript.record_user(self.cfg, user_message=text, session_id=f"s{hour}", turn_id=str(hour),
                               ts=_ts(self.day, hour), **kw)

    def test_dm_cli_and_shared(self):
        self.user("[draft] here's my idea", 8, platform="telegram", chat_type="dm")
        self.user("[1] first point", 9, platform="telegram", chat_type="dm", name="Robin")
        self.user("[note] typed at the terminal", 10, platform="cli")
        self.user("[Sam] hello channel", 11, platform="discord", chat_type="channel")
        self.user("[Kim] hello group", 12, platform="telegram", chat_type="group")
        self.user("[Lee] hello thread", 13, platform="discord", chat_type="thread")
        self.user("[Heartbeat — recurring]\nGo.", 14, platform="telegram", chat_type="dm")
        text = self.render()
        self.assertIn("### User (2026-03-04 08:00:00)\n\n[draft] here's my idea", text)
        self.assertIn("### Robin (2026-03-04 09:00:00)\n\n[1] first point", text)
        self.assertIn("### User (2026-03-04 10:00:00)\n\n[note] typed at the terminal", text)
        self.assertIn("### Sam (2026-03-04 11:00:00)", text)
        self.assertIn("### Kim (2026-03-04 12:00:00)", text)
        self.assertIn("### Lee (2026-03-04 13:00:00)", text)
        self.assertIn("### Heartbeat (2026-03-04 14:00:00)", text)
        for wrong in ("### draft", "### 1 ", "### note"):
            self.assertNotIn(wrong, text)

    def test_rows_from_before_the_change_keep_the_old_rule(self):
        folder = Path(self.cfg["journal"])
        folder.mkdir(parents=True)
        row = {"v": 1, "kind": "user", "ts": _ts(self.day, 8), "session": "g", "turn": "a", "platform": "discord",
               "sender": "", "name": "", "chat": "", "text": "[Robin] older row", "keys": []}
        (folder / f"{self.day.isoformat()}.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
        self.assertIn("### Robin (", self.render())

    def test_shared_chat_rule_matches_hermes(self):
        for value, shared in (("dm", False), ("", False), (None, False), ("DM", False),
                              ("group", True), ("channel", True), ("thread", True)):
            self.assertEqual(transcript.shared_chat(value), shared, value)
        self.assertEqual(transcript.speaker_for("[draft] x", "User", shared=False), "User")
        self.assertEqual(transcript.speaker_for("[draft] x", "User", shared=True), "draft")
        self.assertIsNone(transcript.speaker_for("[Recent channel messages]\n[a] b", "User", shared=False))

    def test_session_identity_reads_the_bound_chat_type(self):
        env = _fake_env(HERMES_SESSION_PLATFORM="discord", HERMES_SESSION_USER_NAME="Robin",
                        HERMES_SESSION_CHAT_NAME="Garden / #beds", HERMES_SESSION_CHAT_TYPE="group")
        with mock.patch.object(transcript, "_session_env", env):
            self.assertEqual(transcript.session_identity("discord"),
                             {"name": "Robin", "chat": "Garden / #beds", "chat_type": "group"})
            self.assertEqual(transcript.session_identity("cli"), {})

    def test_outside_hermes_there_is_no_chat_type(self):
        with mock.patch.object(transcript, "_session_env", lambda: None):
            self.assertEqual(transcript.session_identity("discord"), {})


# --- 4. get_session_env stays, with the guide cited --------------------------------------------------

class SessionEnvCommentTest(unittest.TestCase):
    def test_import_cites_the_plugin_guide(self):
        source = (ROOT / "transcript.py").read_text(encoding="utf-8")
        at = source.index("from gateway.session_context import get_session_env")
        before = source[max(0, at - 700):at]
        self.assertIn("developer-guide/plugins/index.md", before)
        self.assertIn("L1792", before)


# --- hook and provider copies for 5 and 6 -----------------------------------------------------------

class _HookCase(unittest.TestCase):
    extra = ""

    def setUp(self):
        sys.modules.pop(_SHARED_NAME, None)
        self.addCleanup(sys.modules.pop, _SHARED_NAME, None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.notes = self.dir / "suggestions"
        self.asked: list = []
        self.key = "test-key"
        self.env: dict = {}

    def questions(self, extra: str = "", provider: str = "no") -> Path:
        path = self.dir / f"questions-{provider}.txt"
        path.write_text(
            (ROOT / "questions.txt").read_text(encoding="utf-8-sig")
            + f"\nprovider = {provider}\nsuggestions.folder = {self.notes.as_posix()}\n"
            + f"transcript.folder = {(self.dir / 't').as_posix()}\ntranscript.journal = {(self.dir / 'j').as_posix()}\n"
            + "finalize = no\n" + extra,
            encoding="utf-8",
        )
        return path

    def copy(self, prefix: str):
        pkg, name = _load_copy(prefix)
        self.addCleanup(lambda: [sys.modules.pop(k, None) for k in list(sys.modules)
                                 if k == name or k.startswith(name + ".")])

        def ask(message, items, timeout, **kwargs):
            self.asked.append(len(items))
            return {item["id"]: 0.9 for item in items}

        patches = [
            mock.patch.object(pkg, "jev_ask", ask),
            mock.patch.object(sys.modules[f"{name}.jev_client"], "ask", ask),
            mock.patch.object(sys.modules[f"{name}.jev_client"], "api_key", lambda: self.key),
            mock.patch.object(sys.modules[f"{name}.transcript"], "_session_env",
                              lambda: (lambda n, d="": self.env.get(n, d))),
            mock.patch.object(sys.modules[f"{name}.suggestions"], "refresh_transcript", lambda target: None),
            mock.patch.object(pkg, "_provider_setting", lambda: None),
            mock.patch.object(pkg, "start_finalizer", lambda *a, **k: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return pkg, name

    def hooks(self, path: Path):
        pkg, name = self.copy("nwr_decisions_hook")
        ctx = _Ctx(path)
        pkg.register(ctx)
        self.addCleanup(lambda: sys.modules[f"{name}.suggestions"].wait_for_refresh(10.0))
        return pkg, ctx

    def rows(self) -> list[dict]:
        return [json.loads(line) for f in sorted(self.notes.glob("*.jsonl"))
                for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]

    def journal(self) -> list[dict]:
        return [json.loads(line) for f in sorted((self.dir / "j").glob("*.jsonl"))
                for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- 5. no API key ----------------------------------------------------------------------------------

class NoKeyTest(_HookCase):
    def test_no_block_no_row_one_warning(self):
        self.key = ""
        path = self.questions()
        with self.assertLogs("nyx-wiki-recall", logging.WARNING) as logs:
            pkg, ctx = self.hooks(path)
            pkg.register(_Ctx(path))  # a plugin reload in the same process
            pre = ctx.hooks["pre_llm_call"]
            for turn in range(3):
                self.assertIsNone(pre(user_message=MESSAGE, conversation_history=[], session_id="s1",
                                      turn_id=str(turn), platform="cli"))
        key_lines = [line for line in logs.output if "OPENROUTER_API_KEY" in line]
        self.assertEqual(len(key_lines), 1, logs.output)
        self.assertIn("lookups are off", key_lines[0])
        self.assertEqual(len(logs.output), 1, logs.output)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.rows(), [])
        self.assertEqual(len([r for r in self.journal() if r["kind"] == "user"]), 3, "transcripts still run")

    def test_provider_mode_without_a_key(self):
        self.key = ""
        path = self.questions(provider="yes")
        pkg, name = self.copy("nwr_decisions_memory")
        provider = pkg.WikiRecallProvider(questions_path=str(path), wiki_path=str(WIKI))
        provider.initialize("s1", platform="cli")
        self.addCleanup(provider.shutdown)
        self.assertEqual(provider.prefetch(MESSAGE, session_id="s1"), "")
        self.assertEqual(self.asked, [])
        self.assertEqual(self.rows(), [])

    def test_with_a_key_lookups_run(self):
        _pkg, ctx = self.hooks(self.questions())
        out = ctx.hooks["pre_llm_call"](user_message=MESSAGE, conversation_history=[], session_id="s1", platform="cli")
        self.assertIn("soil", out["context"].lower())
        self.assertEqual(len(self.rows()), 1)


# --- 6. cron turns -----------------------------------------------------------------------------------

class CronTest(_HookCase):
    def test_cron_platform_gets_no_lookup_by_default(self):
        _pkg, ctx = self.hooks(self.questions())
        self.assertIsNone(ctx.hooks["pre_llm_call"](user_message=MESSAGE, conversation_history=[],
                                                    session_id="c1", platform="cron"))
        self.assertEqual(self.asked, [])
        self.assertEqual(self.rows(), [])

    def test_cron_session_flag_also_counts(self):
        self.env = {"HERMES_CRON_SESSION": "1"}
        _pkg, ctx = self.hooks(self.questions())
        self.assertIsNone(ctx.hooks["pre_llm_call"](user_message=MESSAGE, conversation_history=[],
                                                    session_id="c1", platform="discord"))
        self.assertEqual(self.asked, [])

    def test_lookups_on_cron_yes_turns_them_back_on(self):
        _pkg, ctx = self.hooks(self.questions("lookups.on_cron = yes\n"))
        out = ctx.hooks["pre_llm_call"](user_message=MESSAGE, conversation_history=[], session_id="c1",
                                        platform="cron")
        self.assertIn("soil", out["context"].lower())
        self.assertEqual(len(self.rows()), 1)

    def test_other_turns_are_unchanged(self):
        _pkg, ctx = self.hooks(self.questions())
        self.assertTrue(ctx.hooks["pre_llm_call"](user_message=MESSAGE, conversation_history=[], session_id="s1",
                                                  platform="discord"))
        self.assertTrue(self.asked)

    def test_cron_turns_are_recorded_without_a_lookup(self):
        # Shipped: transcript.skip.platforms = subagent, so a cron turn is recorded, still with no lookup.
        _pkg, ctx = self.hooks(self.questions())
        ctx.hooks["pre_llm_call"](user_message=MESSAGE, conversation_history=[], session_id="c1", platform="cron")
        self.assertEqual([r["platform"] for r in self.journal()], ["cron"])
        self.assertEqual(self.asked, [])

    def test_cron_can_still_be_skipped(self):
        _pkg, ctx = self.hooks(self.questions("transcript.skip.platforms = cron, subagent\n"))
        ctx.hooks["pre_llm_call"](user_message=MESSAGE, conversation_history=[], session_id="c2", platform="cron")
        self.assertEqual(self.journal(), [])

    def test_provider_on_a_cron_agent(self):
        path = self.questions(provider="yes")
        pkg, _name = self.copy("nwr_decisions_memory")
        provider = pkg.WikiRecallProvider(questions_path=str(path), wiki_path=str(WIKI))
        # What Hermes passes a provider on a cron agent (agent/agent_init.py _memory_provider_init_kwargs).
        provider.initialize("c1", platform="cron", agent_context="cron")
        self.addCleanup(provider.shutdown)
        self.assertEqual(provider.prefetch(MESSAGE, session_id="c1"), "")
        self.assertEqual(self.asked, [])
        other = pkg.WikiRecallProvider(questions_path=str(path), wiki_path=str(WIKI))
        other.initialize("s1", platform="discord", agent_context="primary")
        self.addCleanup(other.shutdown)
        self.assertIn("soil", other.prefetch(MESSAGE, session_id="s1").lower())


# --- 3 through the hook ------------------------------------------------------------------------------

class HookChatTypeTest(_HookCase):
    def test_the_hook_notes_the_bound_chat_type(self):
        _pkg, ctx = self.hooks(self.questions())
        self.env = {"HERMES_SESSION_PLATFORM": "telegram", "HERMES_SESSION_CHAT_TYPE": "dm",
                    "HERMES_SESSION_USER_NAME": "Robin"}
        ctx.hooks["pre_llm_call"](user_message="[draft] my idea", conversation_history=[], session_id="d1",
                                  turn_id="1", platform="telegram")
        self.env = {"HERMES_SESSION_PLATFORM": "discord", "HERMES_SESSION_CHAT_TYPE": "channel"}
        ctx.hooks["pre_llm_call"](user_message="[Sam] hi all", conversation_history=[], session_id="g1",
                                  turn_id="2", platform="discord")
        rows = [r for r in self.journal() if r["kind"] == "user"]
        self.assertEqual([(r["chat_type"], r["name"]) for r in rows], [("dm", "Robin"), ("channel", "")])


if __name__ == "__main__":
    unittest.main()
