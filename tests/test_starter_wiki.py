"""The starter wiki in templates/ parses, every visible folder has a glimpse, private folders stay hidden."""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from questions import load
from wiki_tree import all_articles, load_wiki

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "starter-wiki"
QUESTIONS = load(ROOT / "questions.txt")


def _visible_folders(folder):
    for child in folder.folders:
        yield child
        yield from _visible_folders(child)


class StarterWikiTest(unittest.TestCase):
    def setUp(self):
        self.wiki = load_wiki(str(TEMPLATE), QUESTIONS)
        self.assertIsNotNone(self.wiki)

    def test_top_folders(self):
        self.assertEqual([f.rel for f in self.wiki.folders], ["events", "people", "projects", "topics"])

    def test_every_visible_folder_has_a_marked_glimpse(self):
        opener = QUESTIONS.get("glimpse.open")
        closer = QUESTIONS.get("glimpse.close")
        about = QUESTIONS.get("about.name")
        folders = list(_visible_folders(self.wiki))
        self.assertTrue(folders)
        for folder in folders:
            with self.subTest(folder=folder.rel):
                self.assertTrue(folder.glimpse.strip())
                text = (TEMPLATE / folder.rel / about).read_text(encoding="utf-8")
                self.assertIn(opener, text)
                self.assertIn(closer, text)
                self.assertIn(folder.glimpse, text.split(opener, 1)[1].split(closer, 1)[0])

    def test_drafts_sources_and_root_guides_are_not_pages(self):
        rels = [f.rel for f in _visible_folders(self.wiki)]
        self.assertFalse(any(rel.split("/")[0].startswith("_") for rel in rels))
        for name in ("_drafts", "_sources", "SCHEMA.md", "index.md", "log.md"):
            self.assertTrue((TEMPLATE / name).exists(), name)
        self.assertEqual(all_articles(self.wiki), [])

    def test_no_installation_names(self):
        banned = re.compile(r"\b(Nyx|Marco|Clotho|Asteria|Athene|Iris|Kaelen|studio)\b", re.IGNORECASE)
        for path in TEMPLATE.rglob("*"):
            if path.is_file():
                with self.subTest(path=path.name):
                    text = path.read_text(encoding="utf-8").replace("nyx-wiki-recall", "").replace("NyxWikiRecall", "")
                    self.assertIsNone(banned.search(text))


class ArchivistSkillTest(unittest.TestCase):
    def test_frontmatter_and_registration(self):
        path = ROOT / "skills" / "wiki-archivist" / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        head = text.split("\n---\n", 1)[0]
        self.assertIn("\nname: wiki-archivist\n", head + "\n")
        self.assertIn("\ndescription: ", head)
        self.assertIn('register_skill("wiki-archivist"', (ROOT / "__init__.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
