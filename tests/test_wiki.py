import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from questions import load
from wiki_tree import find_articles, load_wiki

ROOT = Path(__file__).resolve().parents[1]
WIKI = ROOT / "examples" / "wiki"
QUESTIONS = load(ROOT / "questions.txt")


class WikiTreeTest(unittest.TestCase):
    def test_top_level_folders_and_skips(self):
        wiki = load_wiki(str(WIKI), QUESTIONS)
        names = [folder.rel for folder in wiki.folders]
        self.assertEqual(names, ["garden", "notes", "sources"])
        self.assertNotIn("_archive", names)

    def test_garden_children_are_articles_and_one_folder(self):
        wiki = load_wiki(str(WIKI), QUESTIONS)
        garden = wiki.folders[0]
        self.assertEqual([article.rel for article in garden.articles], ["garden/soil.md", "garden/welcome.md"])
        self.assertEqual([folder.rel for folder in garden.folders], ["garden/beds"])
        beds = garden.folders[0]
        self.assertEqual([article.rel for article in beds.articles], ["garden/beds/tomato.md"])
        self.assertEqual([folder.rel for folder in beds.folders], ["garden/beds/rows"])

    def test_glimpse_and_full_file(self):
        wiki = load_wiki(str(WIKI), QUESTIONS)
        soil = find_articles(wiki, path="garden/soil.md")[0]
        self.assertEqual(soil.glimpse, "Soil is fed with compost in spring.")
        self.assertIn("turned once in March", soil.file_text)
        self.assertNotIn("turned once in March", soil.glimpse)

    def test_page_without_markers_uses_the_first_paragraph(self):
        wiki = load_wiki(str(WIKI), QUESTIONS)
        plain = find_articles(wiki, path="notes/plain.md")[0]
        self.assertEqual(
            plain.glimpse,
            "The first paragraph is the summary when a page has no glimpse markers.",
        )
        self.assertIn("The second paragraph stays in the article.", plain.file_text)
        self.assertNotIn("second paragraph", plain.glimpse)

    def test_root_markdown_is_not_an_article(self):
        wiki = load_wiki(str(WIKI), QUESTIONS)
        self.assertEqual(find_articles(wiki, slug="SCHEMA"), [])

    def test_missing_path(self):
        self.assertIsNone(load_wiki("", QUESTIONS))
