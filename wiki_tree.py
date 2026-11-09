"""Read a markdown wiki. Folders are categories. Articles are the other .md files."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

if __package__:
    from .questions import Questions
else:
    from questions import Questions

_cache: dict[tuple, "Folder"] = {}


@dataclass(frozen=True)
class Article:
    rel: str
    title: str
    slug: str
    glimpse: str
    file_text: str


@dataclass(frozen=True)
class Folder:
    rel: str
    title: str
    glimpse: str
    folders: tuple["Folder", ...]
    articles: tuple[Article, ...]


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip("\"'")
    body = text[end + 4 :]
    if body.startswith("\n"):
        body = body[1:]
    return meta, body


def _marked_glimpse(text: str, questions: Questions) -> str:
    opener = questions.get("glimpse.open", "<!-- glimpse -->")
    closer = questions.get("glimpse.close", "<!-- /glimpse -->")
    start = text.find(opener)
    if start < 0:
        return ""
    start += len(opener)
    end = text.find(closer, start)
    if end < 0:
        return ""
    return text[start:end].strip()


def _first_paragraph(text: str) -> str:
    """The first paragraph after the title block, when a page has no glimpse markers."""
    _meta, body = _frontmatter(text)
    lines: list[str] = []
    started = False
    for line in body.splitlines():
        stripped = line.strip()
        if not started:
            if not stripped or stripped.startswith("#"):
                continue
            started = True
        if not stripped:
            break
        lines.append(stripped)
    return " ".join(lines).strip()


def _glimpse(text: str, questions: Questions) -> str:
    marked = _marked_glimpse(text, questions)
    if marked:
        return marked
    return _first_paragraph(text)


def _title(meta: dict[str, str], body: str, fallback: str) -> str:
    if meta.get("title"):
        return meta["title"]
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def _hidden(name: str, questions: Questions) -> bool:
    if not questions.flag("skip.hidden", True):
        return False
    return name.startswith(".") or name.startswith("_")


def _read_folder(root: Path, directory: Path, rel: str, questions: Questions) -> Folder:
    about_name = questions.get("about.name", "_about.md")
    skip_dirs = {name.lower() for name in questions.lines("skip.dir")}
    folders: list[Folder] = []
    articles: list[Article] = []
    about_text = ""
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        names = []
    for name in names:
        path = directory / name
        if not _inside(root, path):
            continue
        if path.is_dir():
            if _hidden(name, questions) or name.lower() in skip_dirs:
                continue
            child_rel = f"{rel}/{name}" if rel else name
            folders.append(_read_folder(root, path, child_rel, questions))
            continue
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        # A markdown file in the wiki root is a guide to the wiki, not an article.
        if rel == "":
            continue
        if name == about_name:
            try:
                about_text = path.read_text(encoding="utf-8-sig")
            except OSError:
                about_text = ""
            continue
        if _hidden(name, questions):
            continue
        try:
            file_text = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        meta, body = _frontmatter(file_text)
        article_rel = f"{rel}/{name}" if rel else name
        articles.append(Article(
            rel=article_rel.replace("\\", "/"),
            title=_title(meta, body, path.stem),
            slug=path.stem,
            glimpse=_glimpse(file_text, questions),
            file_text=file_text,
        ))
    about_meta, about_body = _frontmatter(about_text)
    folder_name = Path(rel).name if rel else root.name
    return Folder(
        rel=rel.replace("\\", "/"),
        title=_title(about_meta, about_body, folder_name),
        glimpse=_glimpse(about_text, questions),
        folders=tuple(folders),
        articles=tuple(articles),
    )


def _stamp(root: Path, questions: Questions) -> tuple:
    skip_dirs = {name.lower() for name in questions.lines("skip.dir")}
    rows: list[tuple[str, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if not _hidden(name, questions) and name.lower() not in skip_dirs
            and _inside(root, Path(dirpath) / name)
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if not _inside(root, path):
                continue
            try:
                rows.append((str(path), path.stat().st_mtime_ns))
            except OSError:
                continue
    rows.sort()
    return tuple(rows)


def load_wiki(wiki_path: str, questions: Questions) -> Folder | None:
    """Return the wiki root, or None when wiki_path is missing."""
    if not wiki_path:
        return None
    root = Path(wiki_path).expanduser()
    try:
        root = root.resolve()
    except OSError:
        return None
    if not root.is_dir():
        return None
    stamp = _stamp(root, questions)
    key = (
        str(root),
        stamp,
        questions.flag("skip.hidden", True),
        tuple(questions.lines("skip.dir")),
        questions.get("about.name"),
        questions.get("glimpse.open"),
        questions.get("glimpse.close"),
    )
    cached = _cache.get(key)
    if cached is not None:
        return cached
    folder = _read_folder(root, root, "", questions)
    # Keep one tree per wiki folder. Every edit changes the stamp, so older trees are dropped
    # instead of piling up for the life of the Hermes process.
    for old in list(_cache):
        if old[0] == key[0]:
            _cache.pop(old, None)
    _cache[key] = folder
    return folder


def all_articles(folder: Folder) -> list[Article]:
    found = list(folder.articles)
    for child in folder.folders:
        found.extend(all_articles(child))
    return found


def find_articles(folder: Folder, slug: str = "", path: str = "") -> list[Article]:
    articles = all_articles(folder)
    if path:
        wanted = path.replace("\\", "/").lstrip("/")
        return [article for article in articles if article.rel == wanted]
    if slug:
        return [article for article in articles if article.slug == slug]
    return []
