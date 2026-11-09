import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jev_client import build_body, parse_answers, score_gap
from questions import clear_cache, load, parse
from recall import attach, read_article, should_skip
from wiki_tree import load_wiki

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "examples" / "wiki"


def questions():
    return load(ROOT / "questions.txt")


def wiki(q=None):
    return load_wiki(str(WIKI), q or questions())


class RecordingAsk:
    def __init__(self, scores):
        self.scores = scores
        self.calls = []

    def __call__(self, message, items, timeout):
        self.calls.append({
            "message": message,
            "timeout": timeout,
            "items": [(item["id"], item["instructions"]) for item in items],
        })
        found = {}
        for item in items:
            for rel, score in self.scores.items():
                if f"[{rel}]" in item["instructions"] or f"[{rel}/" in item["instructions"]:
                    # Match the label's [rel] exactly by looking at the label line.
                    pass
            label_line = ""
            for line in item["instructions"].splitlines():
                if line.startswith("Folder:") or line.startswith("Article:"):
                    label_line = line
            for rel, score in self.scores.items():
                if f"[{rel}]" in label_line:
                    found[item["id"]] = score
        return found


class SkipTest(unittest.TestCase):
    def test_short_replies_match_the_file(self):
        q = questions()
        for text in ("", "   ", "/help", "thanks", "Thanks!", "hi", "ok", "yes", "done"):
            self.assertTrue(should_skip(text, q), text)

    def test_ordinary_message_is_kept(self):
        self.assertFalse(should_skip("How do the tomatoes get planted?", questions()))

    def test_shipped_file_names_no_studio(self):
        # Deliberate: the shipped settings must not name one studio's people, rooms, or folders.
        text = (ROOT / "questions.txt").read_text(encoding="utf-8")
        text = text.replace("NyxWikiRecall", "").replace("nyx-wiki-recall", "")
        for word in ("Kaelen", "Fableforge", "Nyx", "Marco", "Iris", "Asteria", "D:\\", "D:/", "drath"):
            self.assertNotIn(word, text)

    def test_hermes_own_turns_are_skipped_by_the_shipped_file(self):
        # Heartbeat and background-process turns are Hermes features, with or without a sender tag.
        q = questions()
        self.assertTrue(should_skip("[Heartbeat — recurring instruction, fires every 4h]\nDo it", q))
        self.assertTrue(should_skip("[Robin] [Heartbeat — recurring]", q))
        self.assertTrue(should_skip("[IMPORTANT: Background process abc matched watch pattern]", q))
        self.assertFalse(should_skip("Tell me about the heartbeat of the garden.", q))

    def test_category_pattern_comes_from_the_file(self):
        q = parse("skip.line = ^\\[[^\\]]{1,40}\\] \\[Heartbeat\\b\nskip.empty = no\nskip.slash = no\n")
        self.assertTrue(should_skip("[Robin] [Heartbeat — recurring]", q))
        self.assertFalse(should_skip("[Robin] [Heartbeat — recurring]", parse("")))


class WalkTest(unittest.TestCase):
    def test_short_message_does_not_ask(self):
        ask = RecordingAsk({})
        text = attach("thanks", [], wiki(), questions(), ask)
        self.assertEqual(text, "")
        self.assertEqual(ask.calls, [])

    def test_second_request_has_article_and_folder(self):
        ask = RecordingAsk({"garden": 0.9, "notes": 0.1, "garden/beds": 0.2, "garden/soil.md": 0.2, "garden/welcome.md": 0.1})
        attach("Tell me about the garden.", [], wiki(), questions(), ask)
        self.assertEqual(len(ask.calls), 2)
        second = "\n".join(item[1] for item in ask.calls[1]["items"])
        self.assertIn("[garden/soil.md]", second)
        self.assertIn("[garden/beds]", second)
        self.assertIn("[garden/welcome.md]", second)
        self.assertNotIn("[garden/beds/tomato.md]", second)
        self.assertNotIn("sources", second)
        self.assertNotIn("_archive", second)

    def test_third_request_is_articles_only(self):
        ask = RecordingAsk({
            "garden": 0.9,
            "garden/beds": 0.8,
            "garden/soil.md": 0.2,
            "garden/welcome.md": 0.1,
            "garden/beds/tomato.md": 0.95,
        })
        text = attach("When do tomatoes go in?", [], wiki(), questions(), ask)
        self.assertEqual(len(ask.calls), 3)
        third = "\n".join(item[1] for item in ask.calls[2]["items"])
        self.assertIn("[garden/beds/tomato.md]", third)
        self.assertNotIn("[garden/beds/rows]", third)
        self.assertNotIn("two folders below", third)
        self.assertIn("Tomatoes go in after the last frost.", text)
        self.assertIn("Short name: tomato", text)
        self.assertIn("wiki_read", text)
        self.assertTrue(text.startswith("<!-- wiki-recall -->"))
        self.assertTrue(text.endswith("<!-- /wiki-recall -->"))

    def test_threshold(self):
        ask = RecordingAsk({"notes": 0.5, "notes/welcome.md": 0.49})
        text = attach("A note for a newcomer.", [], wiki(), questions(), ask)
        self.assertEqual(len(ask.calls), 2)
        self.assertNotIn("gate code", text)
        self.assertNotIn("someone new", text)

        ask_high = RecordingAsk({"notes": 0.5, "notes/welcome.md": 0.5})
        text_high = attach("A note for a newcomer.", [], wiki(), questions(), ask_high)
        self.assertIn("someone new to the garden", text_high)

    def test_duplicate_is_asked_then_left_out(self):
        history = [{
            "role": "user",
            "content": "earlier",
            "api_content": "earlier\n\npath: garden/soil.md\nSoil is fed with compost in spring.",
        }]
        ask = RecordingAsk({
            "garden": 0.9,
            "garden/soil.md": 0.9,
            "garden/beds": 0.9,
            "garden/welcome.md": 0.1,
            "garden/beds/tomato.md": 0.9,
        })
        text = attach("Tomatoes, and the soil again.", history, wiki(), questions(), ask)
        second = "\n".join(item[1] for item in ask.calls[1]["items"])
        self.assertIn("Soil is fed with compost in spring.", second)
        self.assertNotIn("### Soil", text)
        self.assertIn("Tomatoes go in after the last frost.", text)
        self.assertIn("These pages were shown in an earlier message, so they are left out here: Soil (garden/soil.md)", text)
        self.assertIn("Wiki pages from earlier messages are still part of this conversation.", text)

    def test_path_without_the_paragraph_is_shown_again(self):
        history = [{"role": "user", "api_content": "path: notes/welcome.md"}]
        ask = RecordingAsk({"notes": 0.9, "notes/welcome.md": 0.9})
        text = attach("The welcome again.", history, wiki(), questions(), ask)
        self.assertIn("A welcome note for someone new to the garden.", text)
        self.assertNotIn("left out here", text)

    def test_paragraph_still_in_view_is_left_out(self):
        history = [{"role": "user", "api_content": "A welcome note for someone new to the garden."}]
        ask = RecordingAsk({"notes": 0.9, "notes/welcome.md": 0.9})
        text = attach("The welcome again.", history, wiki(), questions(), ask)
        self.assertNotIn("### Welcome", text)
        self.assertIn("Welcome (notes/welcome.md)", text)
        self.assertIn("Use a page from an earlier message when it fits the reply.", text)

    def test_report_lists_every_scored_page(self):
        history = [{"role": "user", "api_content": "Soil is fed with compost in spring."}]
        ask = RecordingAsk({
            "garden": 0.9,
            "garden/soil.md": 0.86,
            "garden/beds": 0.2,
            "garden/welcome.md": 0.1,
        })
        report: dict = {}
        text = attach("The soil again.", history, wiki(), questions(), ask, report=report)
        self.assertNotIn("### Soil", text)
        self.assertTrue(report["asked"])
        pages = {page["path"]: page["score"] for page in report["pages"]}
        self.assertEqual(pages, {"garden/soil.md": 0.86})

    def test_report_is_empty_when_nothing_is_relevant(self):
        report: dict = {}
        attach("Hello garden", [], wiki(), questions(), RecordingAsk({"garden": 0.1, "notes": 0.1}), report=report)
        self.assertTrue(report["asked"])
        self.assertFalse(report["ineligible"])
        self.assertEqual(report["pages"], [])

    def test_report_stays_quiet_when_the_message_is_skipped(self):
        report: dict = {}
        text = attach("thanks", [], wiki(), questions(), RecordingAsk({}), report=report)
        self.assertEqual(text, "")
        self.assertFalse(report["asked"])
        self.assertTrue(report["ineligible"])
        self.assertEqual(report["pages"], [])

    def test_shipped_file_keeps_the_hook(self):
        text = (ROOT / "questions.txt").read_text(encoding="utf-8")
        self.assertIn("provider = no", text)
        self.assertIn("\nsuggestions.folder =\n", text)
        self.assertIn("\nkeep.days = 0\n", text)
        self.assertIn("\nlookups.on_cron = no\n", text)
        self.assertNotIn("transcript.script", text)

    def test_sources_absent_when_listed(self):
        q = parse((ROOT / "questions.txt").read_text(encoding="utf-8") + "\nskip.dir = sources\n")
        tree = load_wiki(str(WIKI), q)
        names = [folder.rel for folder in tree.folders]
        self.assertNotIn("sources", names)
        ask = RecordingAsk({"garden": 1, "notes": 1})
        attach("Everything.", [], tree, q, ask)
        first = "\n".join(item[1] for item in ask.calls[0]["items"])
        self.assertNotIn("left unread", first)
        self.assertNotIn("Archived", first)

    def test_budget_already_spent_asks_nothing(self):
        ask = RecordingAsk({"garden": 1})
        text = attach("Garden", [], wiki(), questions(), ask, deadline=0)
        self.assertEqual(text, "")
        self.assertEqual(ask.calls, [])

    def test_notes_under_a_section_stay_out_of_the_section(self):
        parsed = parse(
            "[folder.instructions]\n"
            "Question line\n"
            "\n"
            "# Example of the yes sentence\n"
            "# The glimpse describes the garden.\n"
            "[folder.yes]\n"
            "Yes line\n"
        )
        self.assertEqual(parsed.block("folder.instructions"), "Question line")
        self.assertEqual(parsed.block("folder.yes"), "Yes line")

    def test_shipped_file_sends_two_turns(self):
        text = (ROOT / "questions.txt").read_text(encoding="utf-8")
        self.assertIn("context.turns = 2", text)
        self.assertIn("Is this folder about the same subject as this conversation?", text)
        folder = questions().block("folder.instructions")
        self.assertNotIn("#", folder)
        self.assertNotIn("Garden [garden]", folder)
        self.assertIn("{label}", folder)
        self.assertIn("{glimpse}", folder)
        history = [
            {"role": "user", "content": "oldest turn about soup"},
            {"role": "assistant", "content": "soup is done"},
            {"role": "user", "content": "middle turn about seeds"},
            {"role": "assistant", "content": "seeds are written"},
        ]
        ask = RecordingAsk({"garden": 0.1})
        attach("What about the craft notes?", history, wiki(), questions(), ask)
        message = ask.calls[0]["message"]
        self.assertIn("Earlier message:\nmiddle turn about seeds", message)
        self.assertIn("Reply:\nseeds are written", message)
        self.assertIn("New message:\nWhat about the craft notes?", message)
        self.assertNotIn("soup", message)

    def test_compaction_is_context_not_a_turn(self):
        history = [
            {"role": "user", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] old work stays out"},
            {"role": "assistant", "content": "32 seed prompts are written for the plates."},
            {
                "role": "user",
                "content": "You've reached the maximum number of tool-calling iterations allowed. Stop.",
            },
            {"role": "assistant", "content": "The creative pass covered plates, seeds, and cuts."},
            {"role": "tool", "content": "huge tool payload that JEV should not see"},
        ]
        ask = RecordingAsk({"garden": 0.1})
        attach("Look for the wiki glimpses.", history, wiki(), questions(), ask)
        message = ask.calls[0]["message"]
        self.assertIn("32 seed prompts are written for the plates.", message)
        self.assertIn("The creative pass covered plates, seeds, and cuts.", message)
        self.assertIn("New message:\nLook for the wiki glimpses.", message)
        self.assertNotIn("CONTEXT COMPACTION", message)
        self.assertNotIn("tool-calling iterations", message)
        self.assertNotIn("huge tool payload", message)

    def test_missing_turn_setting_sends_the_current_message(self):
        text = "\n".join(
            line for line in (ROOT / "questions.txt").read_text(encoding="utf-8").splitlines()
            if not line.startswith("context.turns")
        )
        q = parse(text)
        history = [
            {"role": "user", "content": "earlier turn"},
            {"role": "assistant", "content": "earlier reply"},
        ]
        ask = RecordingAsk({"garden": 0.1})
        attach("Only this message.", history, wiki(), q, ask)
        self.assertEqual(ask.calls[0]["message"], "Only this message.")

    def test_prior_reply_survives_a_current_message_skip(self):
        q = parse(
            (ROOT / "questions.txt").read_text(encoding="utf-8")
            + "\nskip.line = ^\\[[^\\]]{1,40}\\] \\[Heartbeat\\b\n"
        )
        history = [
            {"role": "user", "content": "[Robin] [Heartbeat — recurring instruction]\nDo anything"},
            {"role": "assistant", "content": "I painted the plates."},
        ]
        ask = RecordingAsk({"garden": 0.1})
        attach("Tell me about the garden.", history, wiki(), q, ask)
        self.assertIn("I painted the plates.", ask.calls[0]["message"])
        self.assertIn("[Heartbeat", ask.calls[0]["message"])
        skipped = RecordingAsk({"garden": 1})
        text = attach("[Robin] [Heartbeat — recurring instruction]\nDo anything", history, wiki(), q, skipped)
        self.assertEqual(text, "")
        self.assertEqual(skipped.calls, [])

    def test_long_context_keeps_the_message_to_answer(self):
        text = (ROOT / "questions.txt").read_text(encoding="utf-8").replace(
            "message.chars = 20000", "message.chars = 80"
        )
        q = parse(text)
        history = [
            {"role": "user", "content": "X" * 500},
            {"role": "assistant", "content": "Y" * 500},
        ]
        ask = RecordingAsk({"garden": 0.1})
        attach("ANSWER_TOKEN", history, wiki(), q, ask)
        message = ask.calls[0]["message"]
        self.assertLessEqual(len(message), 80)
        self.assertIn("ANSWER_TOKEN", message)
        self.assertNotIn("X" * 40, message)

    def test_scores_are_logged(self):
        ask = RecordingAsk({"notes": 0.5, "notes/welcome.md": 0.1})
        with self.assertLogs("nyx-wiki-recall", level="INFO") as captured:
            attach("A note for a newcomer.", [], wiki(), questions(), ask)
        text = "\n".join(captured.output)
        self.assertIn("Notes [notes] score=0.500 keep", text)
        self.assertIn("Welcome [notes/welcome.md] score=0.100 drop", text)
        self.assertIn("showing none", text)

    def test_questions_file_edit_is_reread(self):
        clear_cache()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "questions.txt"
            path.write_text("[article.instructions]\nFIRST {label}\n[article.yes]\nyes\n[article.no]\nno\n", encoding="utf-8")
            first = load(path)
            self.assertIn("FIRST", first.block("article.instructions"))
            path.write_text("[article.instructions]\nSECOND {label}\n[article.yes]\nyes\n[article.no]\nno\n", encoding="utf-8")
            os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
            second = load(path)
            self.assertIn("SECOND", second.block("article.instructions"))


class ReadToolTest(unittest.TestCase):
    def test_slug_returns_the_whole_file(self):
        text = read_article(wiki(), questions(), slug="soil")
        self.assertIn("turned once in March", text)
        self.assertIn("compost in spring", text)

    def test_ambiguous_slug_lists_paths(self):
        text = read_article(wiki(), questions(), slug="welcome")
        self.assertIn("notes/welcome.md", text)
        self.assertIn("garden/welcome.md", text)
        self.assertNotIn("shed door", text)

    def test_path_chooses_one(self):
        text = read_article(wiki(), questions(), path="notes/welcome.md")
        self.assertIn("shed door", text)


class JevBodyTest(unittest.TestCase):
    def test_body_and_parse(self):
        body = build_body("typesafe/jev-1.13", "hello", [{
            "id": "q0",
            "instructions": "Article: Soil",
            "yes": "relevant",
            "no": "not relevant",
        }])
        self.assertEqual(body["questions"]["q0"]["type"], "noul")
        self.assertEqual(body["state"]["message"], "hello")
        scores = parse_answers({"answers": {"q0": {"type": "noul", "noul": 0.5}}})
        self.assertEqual(scores["q0"], 0.5)
        self.assertEqual(score_gap({"answers": {"q0": {"type": "noul", "noul": 0.5}}}), "")
        self.assertEqual(score_gap({"answers": {"q0": {"type": "choice"}}}), "no noul scores")
        self.assertEqual(score_gap({"ok": True}), "unexpected answer shape")
