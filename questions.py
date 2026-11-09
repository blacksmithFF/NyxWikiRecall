"""Load questions.txt. Wording the agent and JEV see lives in that file, not in code."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_.]*)\s*=\s*(.*)$")
_BLOCK = re.compile(r"^\[([A-Za-z][A-Za-z0-9_.]*)\]\s*$")
_LIST_KEYS = {"skip.line", "skip.short", "skip.dir", "skip.turn", "transcript.alias"}

_cache: dict[str, tuple[int, "Questions"]] = {}


@dataclass
class Questions:
    values: dict[str, str] = field(default_factory=dict)
    lists: dict[str, list[str]] = field(default_factory=dict)
    blocks: dict[str, str] = field(default_factory=dict)
    path: str = ""

    def get(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def lines(self, key: str) -> list[str]:
        return list(self.lists.get(key, []))

    def block(self, key: str, default: str = "") -> str:
        return self.blocks.get(key, default)

    def flag(self, key: str, default: bool = False) -> bool:
        raw = self.values.get(key)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_note(line: str) -> bool:
    """A line inside a section that the editor wrote for themselves: "#", "# text", "#text"."""
    stripped = line.lstrip()
    return stripped.startswith("#") and not stripped.startswith("##")


def fill(template: str, **kwargs: str) -> str:
    """Replace {name} placeholders. Other braces stay as written."""
    text = template
    for key, value in kwargs.items():
        text = text.replace("{" + key + "}", value)
    return text


def parse(text: str, path: str = "") -> Questions:
    questions = Questions(path=path)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        block = _BLOCK.match(line.strip())
        if block:
            name = block.group(1)
            index += 1
            body: list[str] = []
            while index < len(lines):
                nxt = lines[index]
                if _BLOCK.match(nxt.strip()) or _KEY.match(nxt):
                    break
                # A note under a section is still a note. The line is for the editor.
                # "##" or more starts a markdown heading, which is section text: "### {title}".
                if _is_note(nxt):
                    index += 1
                    continue
                body.append(nxt)
                index += 1
            questions.blocks[name] = "\n".join(body).strip("\n")
            continue
        key = _KEY.match(line)
        if key:
            name, value = key.group(1), key.group(2).strip()
            if name in _LIST_KEYS:
                if value:
                    questions.lists.setdefault(name, []).append(value)
            else:
                questions.values[name] = value
            index += 1
            continue
        index += 1
    return questions


def load(path: Path) -> Questions:
    """Read path. A second read with the same mtime returns the cached copy."""
    file = Path(path)
    stamp = file.stat().st_mtime_ns
    key = str(file.resolve())
    cached = _cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    questions = parse(file.read_text(encoding="utf-8-sig"), path=key)
    _cache[key] = (stamp, questions)
    return questions


def clear_cache() -> None:
    _cache.clear()
