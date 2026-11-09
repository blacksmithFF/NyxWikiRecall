"""JEV timing out or failing is recorded as not scored, apart from none (scored, nothing fit) and ineligible."""

import json
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jev_client import Scores, ask, post_json
from questions import load, parse
from recall import attach
from suggestions import day_file, load_index, record_turn
from transcript import wiki_lines
from wiki_tree import load_wiki

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "examples" / "wiki"


def q():
    return load(ROOT / "questions.txt")


def raising(exc):
    def opener(request, timeout):
        raise exc
    return opener


class JevErrorTest(unittest.TestCase):
    def test_timeout_says_timeout(self):
        got = post_json("https://x", {}, "k", 1.0, opener=raising(urllib.error.URLError(TimeoutError("timed out"))))
        self.assertEqual(got, {})
        self.assertEqual(got.error, "timeout")
        got = post_json("https://x", {}, "k", 1.0, opener=raising(TimeoutError("timed out")))
        self.assertEqual(got.error, "timeout")

    def test_http_error_and_no_key(self):
        err = urllib.error.HTTPError("https://x", 503, "busy", {}, None)
        self.assertEqual(post_json("https://x", {}, "k", 1.0, opener=raising(err)).error, "HTTP 503")
        self.assertEqual(ask("m", [{"id": "f0"}], 1.0, model="m", url="u", key="").error, "no key")
        self.assertEqual(ask("m", [], 1.0, model="m", url="u", key="k").error, "")


class AttachErrorTest(unittest.TestCase):
    def run_attach(self, answer, budget="20"):
        questions = q()
        questions.values["budget.seconds"] = budget
        report = {}
        text = attach("How do the tomatoes get planted in the garden soil?", [], load_wiki(str(WIKI), questions),
                      questions, answer, report=report)
        return text, report

    def test_timeout_is_not_scored(self):
        text, report = self.run_attach(lambda m, items, t: Scores(error="timeout"))
        self.assertEqual(text, "")
        self.assertTrue(report["asked"])
        self.assertEqual(report["error"], "folders: timeout")
        self.assertEqual(report["pages"], [])

    def test_exception_is_not_scored(self):
        def boom(m, items, t):
            raise RuntimeError("down")
        _text, report = self.run_attach(boom)
        self.assertTrue(report["asked"])
        self.assertEqual(report["error"], "folders: RuntimeError")

    def test_budget_spent_is_not_scored(self):
        _text, report = self.run_attach(lambda m, items, t: {}, budget="0")
        self.assertTrue(report["asked"])
        self.assertIn("timeout", report["error"])

    def test_scored_nothing_is_none_not_an_error(self):
        _text, report = self.run_attach(lambda m, items, t: {item["id"]: 0.1 for item in items})
        self.assertTrue(report["asked"])
        self.assertEqual(report["error"], "")
        self.assertEqual(report["pages"], [])


class RowAndTranscriptTest(unittest.TestCase):
    def test_row_and_rendered_label(self):
        questions = parse("suggestions.folder =\n")
        with tempfile.TemporaryDirectory() as tmp:
            questions.values["suggestions.folder"] = tmp
            path = day_file(tmp, date.today())
            record_turn(questions, "timed out message", {"asked": True, "pages": [], "error": "folders: timeout"})
            record_turn(questions, "budget message", {"asked": False, "pages": [], "error": "folders: timeout"})
            record_turn(questions, "scored message", {"asked": True, "pages": [], "error": ""})
            record_turn(questions, "[X] [Heartbeat] hi", {"asked": False, "ineligible": True, "error": ""})
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertEqual([r["asked"] for r in rows], [True, True, True, False])
            self.assertEqual([r.get("error") for r in rows], ["folders: timeout", "folders: timeout", None, None])
            index = load_index(path)
            self.assertEqual(wiki_lines(["timed out message"], index), "Wiki pages:\nnot scored")
            self.assertEqual(wiki_lines(["budget message"], index), "Wiki pages:\nnot scored")
            self.assertEqual(wiki_lines(["scored message"], index), "Wiki pages:\nnone")
            self.assertEqual(wiki_lines(["[X] [Heartbeat] hi"], index), "Wiki pages:\nineligible")
            self.assertEqual(wiki_lines(["never looked at"], index), "")


if __name__ == "__main__":
    unittest.main()