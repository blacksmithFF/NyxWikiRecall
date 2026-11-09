"""max.pages: at most N pages per message, highest score first, the same in both positions of provider."""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from questions import load, parse
from recall import DEFAULT_MAX_PAGES, attach, cap_pages, max_pages
from wiki_tree import load_wiki
from tests.test_toggle import ROOT, WIKI, _Ctx, _SHARED_NAME, _load_copy


def graded(message, items, timeout, **kwargs):
    """Every candidate fits, each with its own score between 0.5 and 0.99."""
    out = {}
    for item in items:
        label = next((l for l in item["instructions"].splitlines() if l.startswith(("Folder:", "Article:"))), "")
        out[item["id"]] = 0.5 + (zlib.crc32(label.encode("utf-8")) % 4900) / 10000
    return out


def art(rel):
    return types.SimpleNamespace(rel=rel)


def q_with(extra=""):
    return parse("threshold = 0.5\n" + extra)


class CapUnitTest(unittest.TestCase):
    def test_setting(self):
        self.assertEqual(DEFAULT_MAX_PAGES, 8)
        self.assertEqual(max_pages(q_with()), 8, "missing: default")
        self.assertEqual(max_pages(q_with("max.pages = 3\n")), 3)
        self.assertEqual(max_pages(q_with("max.pages = 0\n")), 0)
        self.assertEqual(max_pages(q_with("max.pages =\n")), 0, "blank: no cap")
        self.assertEqual(max_pages(q_with("max.pages = 3\nmax.pages = 5\n")), 5, "last value wins")
        self.assertEqual(max_pages(q_with("max.pages = lots\n")), 8)

    def test_public_file_ships_8(self):
        self.assertEqual(load(ROOT / "questions.txt").get("max.pages"), "8")

    def test_cap_keeps_the_highest_scores_in_order(self):
        scores = {f"p{i}.md": s for i, s in enumerate([0.53, 0.89, 0.71, 0.64, 0.83, 0.58, 0.76, 0.67, 0.72, 0.63, 0.57])}
        arts = [art(rel) for rel in scores] + [art("p1.md")]
        got = [a.rel for a in cap_pages(arts, scores, q_with("max.pages = 4\n"))]
        self.assertEqual(got, ["p1.md", "p4.md", "p6.md", "p8.md"])
        every = [a.rel for a in cap_pages(arts, scores, q_with("max.pages = 0\n"))]
        self.assertEqual(len(every), 11, "0: uncapped, duplicates folded")
        self.assertEqual(every, sorted(scores, key=scores.get, reverse=True))

    def test_attach_report_and_render_follow_the_cap(self):
        base = load(ROOT / "questions.txt")
        wiki = load_wiki(str(WIKI), base)
        message = "How is the soil in the garden beds fed, and where are the tomato and plain notes?"
        results = {}
        for cap in ("0", "2"):
            questions = parse((ROOT / "questions.txt").read_text(encoding="utf-8-sig") + f"\nmax.pages = {cap}\n")
            report = {}
            text = attach(message, [], wiki, questions, graded, report=report)
            results[cap] = (report["pages"], text)
        everything, _ = results["0"]
        capped, text = results["2"]
        self.assertGreater(len(everything), 2, "example wiki gives more than the cap")
        self.assertEqual(len(capped), 2)
        top = sorted(everything, key=lambda p: p["score"], reverse=True)[:2]
        self.assertEqual(capped, top)
        self.assertEqual([p["score"] for p in capped], sorted([p["score"] for p in capped], reverse=True))
        self.assertEqual(text.count("File: "), 2, "only the kept pages are shown")
        for page in everything:
            if page not in capped:
                self.assertNotIn(f"File: {page['path']}", text)


class CapBothModesTest(unittest.TestCase):
    def _run(self, provider, cap):
        sys.modules.pop(_SHARED_NAME, None)
        message = "[Helper [bot]] How is the soil in the garden beds fed, and the tomato rows, and the plain notes?"
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            notes = folder / "suggestions"
            path = folder / "questions.txt"
            path.write_text(
                (ROOT / "questions.txt").read_text(encoding="utf-8-sig")
                + f"\nmax.pages = {cap}\nprovider = {provider}\nsuggestions.folder = {notes.as_posix()}\n"
                + "finalize = no\n",
                encoding="utf-8",
            )
            hook_pkg, hook_name = _load_copy("nwr_cap_hook")
            memory_pkg, memory_name = _load_copy("nwr_cap_memory")
            patches = []
            for pkg, name in ((hook_pkg, hook_name), (memory_pkg, memory_name)):
                patches += [
                    mock.patch.object(sys.modules[f"{name}.suggestions"], "refresh_transcript", lambda target: None),
                    mock.patch.object(pkg, "jev_ask", graded),
                    mock.patch.object(sys.modules[f"{name}.jev_client"], "ask", graded),
                    mock.patch.object(sys.modules[f"{name}.jev_client"], "api_key", lambda: "test-key"),
                    mock.patch.object(pkg, "_provider_setting", lambda: None),
                ]
            for patch in patches:
                patch.start()
            try:
                provider_obj = memory_pkg.WikiRecallProvider(questions_path=str(path), wiki_path=str(WIKI))
                provider_obj.initialize("s1")
                ctx = _Ctx(path)
                hook_pkg.register(ctx)
                hook_out = ctx.hooks["pre_llm_call"](user_message=message, conversation_history=[], session_id="s1")
                prefetch_out = provider_obj.prefetch(message, session_id="s1")
                ctx.hooks["post_llm_call"](session_id="s1")
                sys.modules[f"{hook_name}.suggestions"].wait_for_refresh(10.0)
                count = getattr(provider_obj, "_last_count", None) if provider == "yes" else None
                provider_obj.shutdown()
                rows = [json.loads(line) for f in sorted(notes.glob("*.jsonl"))
                        for line in f.read_text(encoding="utf-8").splitlines()]
            finally:
                for patch in patches:
                    patch.stop()
                for key in [k for k in sys.modules if k.split(".")[0] in {hook_name, memory_name}]:
                    sys.modules.pop(key, None)
                sys.modules.pop(_SHARED_NAME, None)
        shown = (hook_out or {}).get("context", "") if provider == "no" else prefetch_out
        return rows, shown, count

    def test_same_pages_in_both_positions(self):
        for cap in ("2", "0"):
            with self.subTest(cap=cap):
                hook_rows, hook_text, _ = self._run("no", cap)
                mem_rows, mem_text, status = self._run("yes", cap)
                self.assertEqual(len(hook_rows), 1)
                self.assertEqual(hook_rows, mem_rows)
                self.assertEqual(hook_text, mem_text)
                if cap == "2":
                    self.assertEqual(len(hook_rows[0]["pages"]), 2)
                    self.assertEqual(hook_text.count("File: "), 2)
                    self.assertEqual(status, 2, "Discord count follows the cap")
                else:
                    self.assertGreater(len(hook_rows[0]["pages"]), 2)


if __name__ == "__main__":
    unittest.main()