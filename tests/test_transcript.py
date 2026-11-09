"""The built-in writer: a fresh Hermes home gets an open day after a reply and a final day after midnight."""

from __future__ import annotations

import json
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
from suggestions import append_record, day_file, index_for_day

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 3, 4)


def ts(hour, minute=0, second=0, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, second).timestamp()


class FreshHome(unittest.TestCase):
    """Each test gets its own empty Hermes home and the shipped questions.txt."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "hermes-home"
        patch = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        patch.start()
        self.addCleanup(patch.stop)
        self.q = parse((ROOT / "questions.txt").read_text(encoding="utf-8-sig"))
        self.cfg = transcript.settings(self.q)
        self.data = self.home / "plugin-data" / "nyx-wiki-recall"

    def turn(self, text, reply, *, at, session="s1", turn="t1", platform="discord", history=None,
             chat_type="channel"):
        """chat_type is what the gateway binds: channel or group for a shared chat, dm, or "" (the CLI)."""
        transcript.record_user(self.cfg, user_message=text, session_id=session, turn_id=turn,
                               platform=platform, ts=at, chat_type=chat_type)
        transcript.record_reply(self.cfg, assistant_response=reply, conversation_history=history,
                                session_id=session, turn_id=turn, platform=platform, ts=at + 5)

    def day_file(self, day=DAY) -> Path:
        return self.data / "transcripts" / transcript.daily_name(day)


class DefaultsTest(FreshHome):
    def test_on_with_no_setup_and_inside_the_hermes_home(self):
        self.assertIsNotNone(self.cfg)
        self.assertEqual(Path(self.cfg["folder"]), self.data / "transcripts")
        self.assertEqual(Path(self.cfg["suggestions"]), self.data / "suggestions")
        self.assertEqual(self.cfg["user"], "User")
        self.assertEqual(self.cfg["assistant"], "Assistant")
        fin = finalize.settings(self.q)
        self.assertEqual(fin["state"], self.data / "finalized.json")
        self.assertNotIn("script", fin)
        self.assertEqual(fin["keep_days"], 0)

    def test_off_and_a_leftover_script_setting_is_ignored(self):
        self.q.values["transcript"] = "no"
        self.assertIsNone(transcript.settings(self.q))
        self.assertIsNone(finalize.settings(self.q))
        self.q.values["transcript"] = "yes"
        # transcript.script was removed: an old questions.txt that still sets it gets the built-in writer.
        self.q.values["transcript.script"] = "/somewhere/my_exporter.py"
        self.assertIsNotNone(transcript.settings(self.q))
        self.assertEqual(Path(finalize.settings(self.q)["writer"]["folder"]), self.data / "transcripts")

    def test_configurable_folder(self):
        self.q.values["transcript.folder"] = str(Path(self.tmp.name) / "elsewhere")
        self.assertEqual(Path(transcript.settings(self.q)["folder"]), Path(self.tmp.name) / "elsewhere")


class OpenThenFinalTest(FreshHome):
    def test_open_day_then_final_day_then_left_alone(self):
        self.turn("[Robin] How is the garden soil fed?", "With compost.", at=ts(9, 15))
        self.assertEqual(transcript.refresh_today(self.cfg, now=datetime(2026, 3, 4, 9, 16)), "wrote")
        text = self.day_file().read_text(encoding="utf-8")
        self.assertIn("**Status:** open", text)
        self.assertIn("### Robin (2026-03-04 09:15:00)", text)
        self.assertIn("How is the garden soil fed?", text)
        self.assertIn("### Assistant (2026-03-04 09:15:05)", text)
        self.assertIn("**Exporter: nyx-wiki-recall**", text)
        self.assertEqual(transcript.refresh_today(self.cfg, now=datetime(2026, 3, 4, 9, 17)), "unchanged")

        state = self.data / "finalized.json"
        state.write_text(json.dumps({"final_through": "2026-03-03", "days": {}}), encoding="utf-8")
        self.assertEqual(finalize.tick(self.q, ROOT, now=datetime(2026, 3, 5, 1, 50)), [])
        self.assertEqual(finalize.tick(self.q, ROOT, now=datetime(2026, 3, 5, 2, 1)), [("2026-03-04", "final")])
        final = self.day_file().read_text(encoding="utf-8")
        self.assertIn("**Status:** final", final)
        self.assertEqual(json.loads(state.read_text())["final_through"], "2026-03-04")

        # A late line for that day, a refresh, or another finalize never touches the final file.
        self.turn("Late words", "Late reply", at=ts(23, 59), turn="t9")
        self.assertEqual(transcript.write_day(self.cfg, DAY, status="open"), "final")
        self.assertEqual(transcript.write_day(self.cfg, DAY, status="final"), "final")
        self.assertEqual(self.day_file().read_text(encoding="utf-8"), final)
        self.assertEqual(finalize.tick(self.q, ROOT, now=datetime(2026, 3, 5, 3, 0)), [])
        index = (self.data / "transcripts" / "transcriptIndex.md").read_text(encoding="utf-8")
        self.assertIn("| 2026-03-04 | `Mar-04-2026-transcript.md` | Final, 2 messages. |", index)

    def test_catch_up_after_downtime_and_empty_days(self):
        for offset, hour in ((0, 10), (2, 11)):
            day = date(2026, 3, 4 + offset)
            self.turn(f"Message on {day}", "ok then", at=ts(hour, day=day), turn=f"t{offset}")
        state = self.data / "finalized.json"
        state.write_text(json.dumps({"final_through": "2026-03-03", "days": {}}), encoding="utf-8")
        got = finalize.tick(self.q, ROOT, now=datetime(2026, 3, 9, 8, 0))
        self.assertEqual(got, [("2026-03-04", "final"), ("2026-03-05", "empty"), ("2026-03-06", "final"),
                               ("2026-03-07", "empty"), ("2026-03-08", "empty")])
        self.assertTrue(self.day_file(date(2026, 3, 6)).is_file())
        self.assertFalse(self.day_file(date(2026, 3, 5)).exists())
        self.assertEqual(finalize.tick(self.q, ROOT, now=datetime(2026, 3, 9, 8, 10)), [])

    def test_catch_up_is_capped(self):
        state = self.data / "finalized.json"
        state.write_text(json.dumps({"final_through": "2026-01-01", "days": {}}), encoding="utf-8")
        got = finalize.tick(self.q, ROOT, now=datetime(2026, 3, 20, 8, 0))
        self.assertEqual([d for d, _ in got], [f"2026-03-{n:02d}" for n in range(13, 20)])

    def test_first_run_seeds_without_writing_old_days(self):
        self.assertEqual(finalize.tick(self.q, ROOT, now=datetime(2026, 3, 5, 9, 0)), [])
        data = json.loads((self.data / "finalized.json").read_text())
        self.assertEqual(data["final_through"], "2026-03-04")

    def test_someone_elses_file_is_never_replaced(self):
        folder = self.data / "transcripts"
        folder.mkdir(parents=True)
        self.day_file().write_text("written by hand\n", encoding="utf-8")
        self.turn("hello there friend", "hi", at=ts(8))
        self.assertEqual(transcript.write_day(self.cfg, DAY, status="open"), "foreign")
        self.assertEqual(self.day_file().read_text(encoding="utf-8"), "written by hand\n")


class SpeakersTest(FreshHome):
    def render(self):
        rows = transcript.read_journal(self.cfg, DAY)
        return transcript.render_day(DAY, rows, self.cfg, status="open", exported_at=datetime(2026, 3, 4, 23, 0))

    def test_platforms_and_speakers(self):
        self.turn("A direct message with no tag.", "Reply one.", at=ts(8), session="dm1", turn="a", platform="telegram",
                  chat_type="dm")
        self.turn("[Robin] Group message from Robin.", "Reply two.", at=ts(9), session="g1", turn="b")
        self.turn("[Sam | Slack user <@U123>] From Slack.", "Reply three.", at=ts(10), session="sl", turn="c",
                  platform="slack")
        self.turn("[Helper [bot]] A bot asks.", "Reply four.", at=ts(11), session="g1", turn="d")
        self.turn("Typed at the terminal.", "Reply five.", at=ts(12), session="cli1", turn="e", platform="cli",
                  chat_type="")
        self.turn("[Robin] [Heartbeat — recurring instruction, fires every 4h]\nDo anything.", "Did a thing.",
                  at=ts(13), session="g1", turn="f")
        text = self.render()
        self.assertIn("## Telegram (session dm1)", text)
        self.assertIn("## Cli (session cli1)", text)
        self.assertIn("### User (2026-03-04 08:00:00)", text)
        self.assertIn("### User (2026-03-04 12:00:00)", text)
        self.assertIn("### Robin (2026-03-04 09:00:00)", text)
        self.assertIn("### Sam (2026-03-04 10:00:00)", text)
        self.assertIn("### Helper (2026-03-04 11:00:00)", text)
        self.assertIn("### Heartbeat (2026-03-04 13:00:00)", text)
        self.assertNotIn("### Robin (2026-03-04 13:00:00)", text)
        self.assertEqual(text.count("### Assistant"), 6)
        self.assertIn("**Source:** Hermes turns recorded by nyx-wiki-recall (cli, discord, slack, telegram)", text)
        for word in ("Nyx", "Marco", "Discord (private)"):
            self.assertNotIn(word, text)

    def test_gateway_bound_name_and_chat(self):
        transcript.record_user(self.cfg, user_message="Hello from a per-user session.", session_id="g2",
                               turn_id="a", platform="discord", ts=ts(8), name="Robin", chat="Garden / #beds",
                               chat_type="group")
        transcript.record_reply(self.cfg, assistant_response="Hi Robin.", session_id="g2", turn_id="a",
                                platform="discord", ts=ts(8, 0, 5))
        transcript.record_user(self.cfg, user_message="[Heartbeat — recurring instruction]\nGo.", session_id="g2",
                               turn_id="b", platform="discord", ts=ts(9), name="Robin", chat="Garden / #beds",
                               chat_type="group")
        text = self.render()
        self.assertIn("## Discord / Garden / #beds", text)
        self.assertIn("### Robin (2026-03-04 08:00:00)", text)
        self.assertIn("### Heartbeat (2026-03-04 09:00:00)", text)
        self.assertEqual(text.count("\n## "), 1, "the reply stays in its session's chat section")
        self.assertEqual(transcript.session_identity("cli"), {})

    def test_names_are_settings(self):
        self.q.values["transcript.user.name"] = "Guest"
        self.q.values["transcript.assistant.name"] = "Helper Bot"
        self.cfg = transcript.settings(self.q)
        self.turn("Hi, how are the beds?", "Fine.", at=ts(8))
        text = self.render()
        self.assertIn("### Guest (", text)
        self.assertIn("### Helper Bot (", text)

    def test_alias_and_step_times(self):
        self.q.lists["transcript.alias"] = ["gardener_42 -> Robin", "broken line"]
        self.cfg = transcript.settings(self.q)
        history = [{"role": "user", "content": "x", "timestamp": ts(8)},
                   {"role": "assistant", "content": "Checking.", "timestamp": ts(8, 0, 2)},
                   {"role": "assistant", "content": "Done.", "timestamp": ts(8, 1)}]
        self.turn("[gardener_42] Water the beds?", "Done.", at=ts(8), history=history)
        text = self.render()
        self.assertIn("### Robin (2026-03-04 08:00:00)", text)
        self.assertIn("### Assistant (2026-03-04 08:00:02)\n\nChecking.", text)
        self.assertIn("### Assistant (2026-03-04 08:01:00)\n\nDone.", text)

    def test_cron_and_subagent_are_not_recorded(self):
        self.turn("Scheduled job prompt", "done", at=ts(8), platform="cron")
        self.turn("Delegated task", "done", at=ts(9), platform="subagent")
        self.assertIsNone(self.render())

    def test_retried_hook_is_one_turn_and_tools_are_listed(self):
        history = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old reply"},
            {"role": "user", "content": "Read my notes please."},
            {"role": "assistant", "content": "Let me look.",
             "tool_calls": [{"function": {"name": "read_file", "arguments": json.dumps({"path": "notes.md"})}}]},
            {"role": "tool", "content": "file text"},
            {"role": "assistant", "content": "Here they are."},
        ]
        self.turn("Read my notes please.", "Here they are.", at=ts(8), history=history)
        transcript.record_user(self.cfg, user_message="Read my notes please.", session_id="s1", turn_id="t1",
                               platform="discord", ts=ts(8, 0, 1))
        text = self.render()
        self.assertEqual(text.count("Read my notes please."), 1)
        self.assertIn("Let me look.", text)
        self.assertIn("  [read_file: notes.md]", text)
        self.assertEqual(text.count("Here they are."), 1)
        self.assertNotIn("old reply", text)

    def test_new_message_and_channel_lines(self):
        pasted = ("[Recent channel messages]\n[Sam] Is anyone watering today?\n[Robin] I watered at noon.\n\n"
                  "[New message]\n[Robin] Agent, what about the tomatoes?")
        self.turn("[Robin] I watered at noon.", "Thanks.", at=ts(12), turn="a")
        self.turn(pasted, "They are fine.", at=ts(12, 30), turn="b")
        text = self.render()
        self.assertIn("[Sam] Is anyone watering today?", text)
        self.assertEqual(text.count("I watered at noon."), 1)
        self.assertIn("Agent, what about the tomatoes?", text)
        self.assertNotIn("[New message]", text)
        self.q.values["transcript.channel_context"] = "no"
        self.cfg = transcript.settings(self.q)
        self.assertNotIn("Is anyone watering today?", self.render())

    def test_secrets_are_redacted_in_the_journal_too(self):
        secret = "my key is sk-abcdefghijklmnopqrstuvwxyz123"
        append_record(day_file(self.cfg["suggestions"], DAY), secret, [], asked=True)
        self.turn(secret, "noted", at=ts(8))
        journal = (Path(self.cfg["journal"]) / f"{DAY.isoformat()}.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("sk-abcdefghijklmnop", journal)
        text = transcript.render_day(DAY, transcript.read_journal(self.cfg, DAY), self.cfg, status="open",
                                     exported_at=datetime(2026, 3, 4, 23),
                                     index=index_for_day(self.cfg["suggestions"], DAY))
        self.assertNotIn("sk-abcdefghijklmnop", text)
        self.assertIn("[redacted]", text)
        self.assertIn("Wiki pages:\nnone", text)

    def test_wiki_pages_lines_from_the_plugins_own_file(self):
        notes = day_file(self.cfg["suggestions"], DAY)
        append_record(notes, "[Robin] How is the soil fed?", [{"path": "garden/soil.md", "score": 0.86}], asked=True)
        append_record(notes, "Nothing fits this.", [], asked=True)
        append_record(notes, "JEV was down.", [], asked=True, error="timeout")
        append_record(notes, "[Robin] [Heartbeat] x", [], asked=False)
        self.turn("[Robin] How is the soil fed?", "Compost.", at=ts(8), turn="a")
        self.turn("Nothing fits this.", "ok", at=ts(9), turn="b")
        self.turn("JEV was down.", "ok", at=ts(10), turn="c")
        self.turn("[Robin] [Heartbeat] x", "ok", at=ts(11), turn="d")
        self.turn("Never looked at.", "ok", at=ts(12), turn="e")
        rows = transcript.read_journal(self.cfg, DAY)
        text = transcript.render_day(DAY, rows, self.cfg, status="open", exported_at=datetime(2026, 3, 4, 23),
                                     index=index_for_day(self.cfg["suggestions"], DAY))
        self.assertIn("Wiki pages:\n1. garden/soil.md (0.86)", text)
        self.assertIn("Nothing fits this.\n\nWiki pages:\nnone", text)
        self.assertIn("JEV was down.\n\nWiki pages:\nnot scored", text)
        self.assertIn("Wiki pages:\nineligible", text)
        self.assertIn("Never looked at.\n\n### Assistant", text)


def _fake_session_env(**values):
    def get_session_env(name, default=""):
        return values.get(name, default)
    return get_session_env


class HookTest(FreshHome):
    """The registered hooks record a turn and refresh today's file with nothing else set up."""

    def test_hooks_write_today(self):
        from tests.test_toggle import WIKI, _SHARED_NAME, _Ctx, _every_candidate_fits, _load_copy

        sys.modules.pop(_SHARED_NAME, None)
        self.addCleanup(sys.modules.pop, _SHARED_NAME, None)
        qpath = Path(self.tmp.name) / "questions.txt"
        qpath.write_text((ROOT / "questions.txt").read_text(encoding="utf-8-sig"), encoding="utf-8")
        pkg, name = _load_copy("nwr_transcript_hook")
        self.addCleanup(lambda: [sys.modules.pop(k, None) for k in list(sys.modules)
                                 if k == name or k.startswith(name + ".")])
        patches = [mock.patch.object(pkg, "jev_ask", _every_candidate_fits),
                   mock.patch.object(sys.modules[f"{name}.jev_client"], "ask", _every_candidate_fits),
                   mock.patch.object(sys.modules[f"{name}.jev_client"], "api_key", lambda: "test-key"),
                   # What the gateway binds for a Telegram group turn (gateway/run.py _set_session_env).
                   mock.patch.object(sys.modules[f"{name}.transcript"], "_session_env", lambda: _fake_session_env(
                       HERMES_SESSION_PLATFORM="telegram", HERMES_SESSION_CHAT_TYPE="group")),
                   mock.patch.object(pkg, "_provider_setting", lambda: None),
                   mock.patch.object(sys.modules[f"{name}.finalize"], "start", lambda *a, **k: None)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        pkg.start_finalizer = lambda *a, **k: None
        ctx = _Ctx(qpath)
        pkg.register(ctx)
        out = ctx.hooks["pre_llm_call"](user_message="[Robin] Tell me how the soil in the garden beds is fed.",
                                        conversation_history=[], session_id="s1", turn_id="t1", platform="telegram")
        self.assertIn("soil", out["context"].lower())
        ctx.hooks["post_llm_call"](session_id="s1", turn_id="t1", platform="telegram",
                                   assistant_response="Compost, mostly.", conversation_history=[])
        self.assertTrue(sys.modules[f"{name}.suggestions"].wait_for_refresh(10.0))
        today = self.data / "transcripts" / transcript.daily_name(datetime.now().date())
        text = today.read_text(encoding="utf-8")
        self.assertIn("**Status:** open", text)
        self.assertIn("### Robin (", text)
        self.assertIn("Compost, mostly.", text)
        self.assertIn("Wiki pages:\n1. ", text)


class ProviderSettingTest(unittest.TestCase):
    def test_disagreement_is_a_warning_and_nothing_is_written(self):
        from tests.test_toggle import _load_copy

        pkg, name = _load_copy("nwr_provider_check")
        self.addCleanup(lambda: [sys.modules.pop(k, None) for k in list(sys.modules)
                                 if k == name or k.startswith(name + ".")])
        self.assertFalse(hasattr(pkg, "sync_installed"))
        self.assertFalse((ROOT / "settings_sync.py").exists())
        q = parse("provider = yes\n")
        pkg._seen_mtime = None
        self.assertIn("memory.provider", pkg._check_provider_setting(q, read_setting=lambda: "holographic"))
        pkg._seen_mtime = None
        self.assertEqual(pkg._check_provider_setting(q, read_setting=lambda: "nyx-wiki-recall"), "")
        q = parse("provider = no\n")
        pkg._seen_mtime = None
        self.assertIn("provider = no", pkg._check_provider_setting(q, read_setting=lambda: "nyx-wiki-recall"))
        pkg._seen_mtime = None
        self.assertEqual(pkg._check_provider_setting(q, read_setting=lambda: ""), "")
        source = "".join(p.read_text(encoding="utf-8") for p in ROOT.glob("*.py"))
        self.assertNotIn("_write_config_value", source)


if __name__ == "__main__":
    unittest.main()
