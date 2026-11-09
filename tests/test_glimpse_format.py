"""The agent sees each glimpse with its title and its match confidence, and notes in questions.txt stay notes."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from questions import load, parse
from recall import attach, render, all_known
from wiki_tree import load_wiki
from tests.test_cap import CapBothModesTest, graded

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "examples" / "wiki"


class HeadingTest(unittest.TestCase):
    def test_headings_are_text_and_notes_are_dropped(self):
        q = parse("[s]\n# a note\n#note\n#\n### {title}\n## Sub\nbody\n  # indented note\n[t]\nx\n")
        self.assertEqual(q.block("s"), "### {title}\n## Sub\nbody")

    def test_shipped_file_keeps_the_title_and_drops_the_notes(self):
        q = load(ROOT / "questions.txt")
        self.assertTrue(q.block("nyx.glimpse").startswith("### {title}\n"))
        self.assertIn("Match confidence: {score}", q.block("nyx.glimpse"))
        text = (ROOT / "questions.txt").read_text(encoding="utf-8-sig")
        notes = [line.strip() for line in text.splitlines() if re.match(r"^\s*#(?!#)", line)]
        self.assertTrue(notes)
        for name, body in q.blocks.items():
            for line in body.splitlines():
                self.assertNotIn(line.strip(), notes, f"note leaked into [{name}]")
                self.assertFalse(re.match(r"^\s*#(?!#)", line), f"note leaked into [{name}]")


class GlimpseFormatTest(unittest.TestCase):
    def setUp(self):
        self.q = load(ROOT / "questions.txt")
        self.wiki = load_wiki(str(WIKI), self.q)
        self.soil = next(a for a in all_known(self.wiki) if a.rel == "garden/soil.md")

    def test_title_and_two_decimal_confidence(self):
        text = render([self.soil], "", self.wiki, self.q, score_of={"garden/soil.md": 0.8567})
        self.assertIn(f"### {self.soil.title}\n", text)
        self.assertIn("Match confidence: 0.86\nFile: garden/soil.md", text)
        self.assertNotIn("(", text.split("Match confidence:")[1].split("\n")[0])

    def test_no_score_leaves_the_line_out(self):
        text = render([self.soil], "", self.wiki, self.q)
        self.assertNotIn("Match confidence", text)
        self.assertNotIn("{score}", text)
        self.assertIn("File: garden/soil.md", text)

    def test_attach_shows_each_kept_page_with_its_score(self):
        report = {}
        text = attach("How is the soil in the garden beds fed, and the tomato rows?", [], self.wiki, self.q,
                      graded, report=report)
        self.assertTrue(report["pages"])
        for page in report["pages"]:
            self.assertIn(f"Match confidence: {page['score']:.2f}\nFile: {page['path']}", text)
        self.assertEqual(text.count("Match confidence: "), len(report["pages"]))
        self.assertEqual(text.count("\n### "), len(report["pages"]))


class BothModesFormatTest(unittest.TestCase):
    def test_same_text_with_titles_and_confidence(self):
        runner = CapBothModesTest()
        hook_rows, hook_text, _ = runner._run("no", "0")
        mem_rows, mem_text, _ = runner._run("yes", "0")
        self.assertEqual(hook_text, mem_text)
        self.assertEqual(hook_rows, mem_rows)
        pages = hook_rows[0]["pages"]
        self.assertEqual(hook_text.count("Match confidence: "), len(pages))
        self.assertEqual(hook_text.count("\n### "), len(pages))


if __name__ == "__main__":
    unittest.main()