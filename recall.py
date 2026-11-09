"""Decide which glimpses to attach. JEV sees every candidate. Duplicates are dropped after JEV answers."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable

if __package__:
    from .questions import Questions, fill
    from .wiki_tree import Article, Folder, all_articles, find_articles
else:
    from questions import Questions, fill
    from wiki_tree import Article, Folder, all_articles, find_articles

log = logging.getLogger("nyx-wiki-recall")

Ask = Callable[[str, list[dict[str, str]], float], dict[str, float]]


@dataclass
class Candidate:
    id: str
    kind: str
    label: str
    glimpse: str
    node: Folder | Article


def message_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text", item.get("content", ""))
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return str(value)


def history_blob(history) -> str:
    parts: list[str] = []
    for message in history or []:
        if isinstance(message, str):
            parts.append(message)
            continue
        if not isinstance(message, dict):
            continue
        for key in ("content", "api_content"):
            parts.append(message_text(message.get(key)))
    return "\n".join(part for part in parts if part)


def _search(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text, re.IGNORECASE) is not None
    except re.error:
        log.warning("nyx-wiki-recall: skip pattern is not valid regex")
        return False


def skip_reason(text: str, questions: Questions) -> str:
    """Why this message gets no lookup. Empty when the message goes to JEV."""
    stripped = text.strip()
    if questions.flag("skip.empty", True) and not stripped:
        return "empty"
    if questions.flag("skip.slash", True) and stripped.startswith("/"):
        return "slash"
    for pattern in questions.lines("skip.short"):
        if _search(pattern, stripped):
            return "short"
    first = stripped.split("\n", 1)[0]
    for pattern in questions.lines("skip.line"):
        if _search(pattern, first):
            return "line"
    return ""


def should_skip(text: str, questions: Questions) -> bool:
    return bool(skip_reason(text, questions))


def _turn_count(questions: Questions) -> int:
    """How many turns JEV sees. A missing or unreadable setting means the current message only."""
    raw = questions.get("context.turns", "").strip()
    if not raw:
        return 1
    try:
        count = int(raw)
    except ValueError:
        return 1
    return count if count >= 1 else 1


def _char_limit(questions: Questions) -> int:
    raw = questions.get("message.chars", "20000")
    try:
        return int(raw)
    except ValueError:
        return 20000


def _cap_head(text: str, questions: Questions) -> str:
    limit = _char_limit(questions)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit]


def _cap_tail(text: str, questions: Questions) -> str:
    """Keep the end. The new message is written last."""
    limit = _char_limit(questions)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[-limit:]


def _dialogue(history) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for message in history or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = message_text(message.get("content")).strip()
        if text:
            rows.append((role, text))
    return rows


def _skipped_turn(text: str, questions: Questions) -> bool:
    first = text.split("\n", 1)[0]
    for pattern in questions.lines("skip.turn"):
        if _search(pattern, first):
            return True
    return False


def _marks(questions: Questions) -> dict[str, str]:
    """Headings for the conversation JEV reads. The questions file uses the same words."""
    def one(key: str, default: str) -> str:
        text = questions.get(key, "").strip()
        return text or default

    return {
        "earlier_message": one("label.earlier_message", "Earlier message"),
        "reply": one("label.reply", "Reply"),
        "new_message": one("label.new_message", "New message"),
    }


def context_for_jev(current: str, history, questions: Questions) -> tuple[str, dict[str, int]]:
    """The conversation text JEV reads. Earlier messages and replies are labeled."""
    current_text = current.strip()
    turns = _turn_count(questions)
    stats = {"turns": turns, "user_messages": 1, "assistant_messages": 0, "truncated": 0}
    if turns <= 1:
        capped = _cap_head(current_text, questions)
        stats["truncated"] = int(len(capped) < len(current_text))
        return capped, stats

    rows = _dialogue(history)
    if rows and rows[-1][0] == "user" and rows[-1][1] == current_text:
        rows = rows[:-1]
    real = [
        index for index, (role, text) in enumerate(rows)
        if role == "user" and not _skipped_turn(text, questions)
    ]
    need_prior = turns - 1
    start = real[-need_prior] if len(real) >= need_prior else 0
    marks = _marks(questions)
    parts: list[str] = []
    turn_no = 0
    user_count = 0
    assistant_count = 0
    for role, text in rows[start:]:
        if role == "user":
            if _skipped_turn(text, questions):
                continue
            turn_no += 1
            user_count += 1
            parts.append(f"{marks['earlier_message']}:\n{text}")
            continue
        if turn_no == 0:
            turn_no = 1
        assistant_count += 1
        parts.append(f"{marks['reply']}:\n{text}")
    if not parts:
        capped = _cap_head(current_text, questions)
        stats["truncated"] = int(len(capped) < len(current_text))
        return capped, stats
    parts.append(f"{marks['new_message']}:\n{current_text}")
    body = "\n\n".join(parts)
    capped = _cap_tail(body, questions)
    stats["user_messages"] = user_count + 1
    stats["assistant_messages"] = assistant_count
    stats["truncated"] = int(len(capped) < len(body))
    return capped, stats


DEFAULT_MAX_PAGES = 8


def max_pages(questions: Questions) -> int:
    """max.pages from questions.txt. 0 or blank means no cap; missing means the default."""
    raw = questions.values.get("max.pages")
    if raw is None:
        return DEFAULT_MAX_PAGES
    raw = raw.strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("nyx-wiki-recall: max.pages %r is not a whole number; using %d", raw, DEFAULT_MAX_PAGES)
        return DEFAULT_MAX_PAGES


def cap_pages(articles: list, score_of: dict[str, float], questions: Questions) -> list:
    """One entry per page, highest score first, at most max.pages."""
    seen: set[str] = set()
    unique = []
    for article in articles:
        if article.rel in seen:
            continue
        seen.add(article.rel)
        unique.append(article)
    unique.sort(key=lambda article: score_of.get(article.rel, 0.0), reverse=True)  # stable on ties
    limit = max_pages(questions)
    if limit and len(unique) > limit:
        log.info(
            "nyx-wiki-recall: capped at %d of %d pages (left out %s)",
            limit,
            len(unique),
            ", ".join(article.rel for article in unique[limit:]),
        )
        unique = unique[:limit]
    return unique


def _label(node: Folder | Article) -> str:
    return f"{node.title} [{node.rel}]"


def _kind_templates(kind: str, questions: Questions) -> tuple[str, str, str]:
    return (
        questions.block(f"{kind}.instructions"),
        questions.block(f"{kind}.yes"),
        questions.block(f"{kind}.no"),
    )


def _questions_for(candidates: list[Candidate], questions: Questions) -> list[dict[str, str]]:
    marks = _marks(questions)
    payload: list[dict[str, str]] = []
    for candidate in candidates:
        instructions, yes, no = _kind_templates(candidate.kind, questions)
        fields = {**marks, "label": candidate.label, "glimpse": candidate.glimpse}
        payload.append({
            "id": candidate.id,
            "instructions": fill(instructions, **fields),
            "yes": fill(yes, **fields),
            "no": fill(no, **fields),
        })
    return payload


def _paragraph_present(blob: str, article: Article) -> bool:
    """True when the glimpse paragraph is still in the messages, not merely the file path."""
    paragraph = (article.glimpse or "").strip()
    if len(paragraph) < 20:
        return False
    return paragraph in blob


def attach(
    user_message,
    history,
    wiki: Folder | None,
    questions: Questions,
    ask: Ask,
    *,
    deadline: float | None = None,
    report: dict | None = None,
) -> str:
    """Return the text to add to the agent's turn. Empty when this message gets no wiki block."""
    started = time.monotonic()
    text = message_text(user_message)
    if report is not None:
        report["asked"] = False
        report["ineligible"] = False
        report["pages"] = []
        report["error"] = ""
    if wiki is None:
        log.info("nyx-wiki-recall: skipped (wiki unavailable)")
        return ""
    reason = skip_reason(text, questions)
    if reason:
        if report is not None:
            report["ineligible"] = True
        log.info("nyx-wiki-recall: skipped (%s)", reason)
        return ""
    if deadline is None:
        try:
            budget = float(questions.get("budget.seconds", "20"))
        except ValueError:
            budget = 20.0
        deadline = time.monotonic() + budget
    message, stats = context_for_jev(text, history, questions)
    log.info(
        "nyx-wiki-recall: context turns=%d user_messages=%d assistant_messages=%d chars=%d",
        stats["turns"],
        stats["user_messages"],
        stats["assistant_messages"],
        len(message),
    )
    if stats["truncated"]:
        log.info("nyx-wiki-recall: context truncated to %d chars", len(message))
    try:
        threshold = float(questions.get("threshold", "0.5"))
    except ValueError:
        threshold = 0.5

    def remaining() -> float:
        return deadline - time.monotonic()

    score_of: dict[str, float] = {}
    asked_jev = False
    # Why scoring stopped short: JEV timed out or failed. Not the same as "scored, nothing fit".
    errors: list[str] = []

    def consult(candidates: list[Candidate], level: str) -> list[Candidate]:
        nonlocal asked_jev
        if not candidates:
            return []
        if remaining() <= 0:
            log.info("nyx-wiki-recall: budget spent before %s (%d candidates)", level, len(candidates))
            errors.append(f"{level}: timeout")
            return []
        log.info(
            "nyx-wiki-recall: asking %s candidates=%d timeout=%.1fs",
            level,
            len(candidates),
            remaining(),
        )
        try:
            scores = ask(message, _questions_for(candidates, questions), remaining())
        except Exception as exc:
            log.warning("nyx-wiki-recall: %s scoring failed (%s)", level, type(exc).__name__)
            errors.append(f"{level}: {type(exc).__name__}")
            return []
        asked_jev = True
        if not scores:
            error = str(getattr(scores, "error", "") or "")
            if error:
                errors.append(f"{level}: {error}")
            log.info("nyx-wiki-recall: %s returned no scores%s", level, f" ({error})" if error else "")
            return []
        chosen: list[Candidate] = []
        for candidate in candidates:
            score = scores.get(candidate.id)
            if score is None:
                log.info("nyx-wiki-recall: %s score missing", candidate.label)
                continue
            keep = score >= threshold
            log.info(
                "nyx-wiki-recall: %s score=%.3f %s",
                candidate.label,
                score,
                "keep" if keep else "drop",
            )
            if keep:
                chosen.append(candidate)
                if isinstance(candidate.node, Article):
                    score_of[candidate.node.rel] = float(score)
        if not chosen:
            log.info("nyx-wiki-recall: %s none reached %.2f", level, threshold)
        return chosen

    roots = [
        Candidate(f"f{index}", "folder", _label(folder), folder.glimpse, folder)
        for index, folder in enumerate(wiki.folders)
        if (folder.glimpse or "").strip()
    ]
    open_roots = [item.node for item in consult(roots, "folders") if isinstance(item.node, Folder)]
    level_two: list[Candidate] = []
    for folder in open_roots:
        for child in folder.folders:
            if not (child.glimpse or "").strip():
                continue
            level_two.append(Candidate(f"c{len(level_two)}", "child", _label(child), child.glimpse, child))
        for article in folder.articles:
            if not (article.glimpse or "").strip():
                continue
            level_two.append(Candidate(f"c{len(level_two)}", "article", _label(article), article.glimpse, article))
    chosen = consult(level_two, "children")
    articles: list[Article] = [item.node for item in chosen if isinstance(item.node, Article)]
    nested: list[Candidate] = []
    for item in chosen:
        if isinstance(item.node, Folder):
            for article in item.node.articles:
                if not (article.glimpse or "").strip():
                    continue
                nested.append(Candidate(f"a{len(nested)}", "article", _label(article), article.glimpse, article))
    articles.extend(item.node for item in consult(nested, "articles") if isinstance(item.node, Article))
    articles = cap_pages(articles, score_of, questions)
    if report is not None:
        report["asked"] = asked_jev or bool(errors)
        report["error"] = "; ".join(errors)
        seen_paths: set[str] = set()
        pages: list[dict] = []
        for article in articles:
            if article.rel in seen_paths:
                continue
            seen_paths.add(article.rel)
            pages.append({"path": article.rel, "score": score_of.get(article.rel, 0.0)})
        report["pages"] = pages
    rendered = render(articles, history_blob(history), wiki, questions, score_of=score_of)
    log.info("nyx-wiki-recall: done in %.2fs attached_chars=%d", time.monotonic() - started, len(rendered))
    return rendered


def _glimpse_template(template: str, score: float | None) -> str:
    """With no score for a page, leave out the template lines that name {score}."""
    if score is not None or "{score}" not in template:
        return template
    return "\n".join(line for line in template.split("\n") if "{score}" not in line)


def render(
    articles: list[Article],
    blob: str,
    wiki: Folder,
    questions: Questions,
    score_of: dict[str, float] | None = None,
) -> str:
    seen: set[str] = set()
    ordered: list[Article] = []
    for article in articles:
        if article.rel in seen:
            continue
        seen.add(article.rel)
        ordered.append(article)
    fresh = [article for article in ordered if not _paragraph_present(blob, article)]
    dropped = [article for article in ordered if _paragraph_present(blob, article)]
    prior = any(_paragraph_present(blob, article) for article in all_known(wiki))
    parts: list[str] = []
    if fresh:
        header = questions.block("nyx.header").strip()
        if header:
            parts.append(header)
        blocks = [
            fill(
                _glimpse_template(questions.block("nyx.glimpse"), (score_of or {}).get(article.rel)),
                title=article.title,
                path=article.rel,
                slug=article.slug,
                glimpse=article.glimpse,
                label=_label(article),
                score=f"{(score_of or {}).get(article.rel, 0.0):.2f}",
            ).strip()
            for article in fresh
        ]
        parts.append("\n\n".join(block for block in blocks if block))
        tool = questions.block("nyx.tool").strip()
        if tool:
            parts.append(tool)
    if (prior or dropped) and (fresh or dropped):
        reminder = questions.block("nyx.reminder").strip()
        if reminder:
            parts.append(reminder)
    if dropped:
        pages = ", ".join(f"{article.title} ({article.rel})" for article in dropped)
        already = fill(questions.block("nyx.already"), pages=pages).strip()
        if already:
            parts.append(already)
    log.info(
        "nyx-wiki-recall: showing %s; already present %s",
        ", ".join(article.rel for article in fresh) or "none",
        ", ".join(article.rel for article in dropped) or "none",
    )
    return _wrap_recall("\n\n".join(part for part in parts if part), questions)


def _wrap_recall(body: str, questions: Questions) -> str:
    """Mark the block so a display can find the glimpses the agent was given."""
    if not body:
        return ""
    open_mark = questions.get("recall.open").strip()
    close_mark = questions.get("recall.close").strip()
    if not open_mark or not close_mark:
        return body
    return f"{open_mark}\n{body}\n{close_mark}"


def all_known(wiki: Folder) -> list[Article]:
    return all_articles(wiki)


def read_article(wiki: Folder, questions: Questions, slug: str = "", path: str = "") -> str:
    query = path or slug
    found = find_articles(wiki, slug=slug, path=path)
    if path:
        if not found:
            return fill(questions.block("nyx.missing"), query=query, slug=slug, path=path).strip()
        return found[0].file_text
    if not slug:
        return fill(questions.block("nyx.missing"), query="", slug="", path="").strip()
    if not found:
        return fill(questions.block("nyx.missing"), query=slug, slug=slug, path="").strip()
    if len(found) > 1:
        paths = "\n".join(article.rel for article in found)
        return fill(questions.block("nyx.ambiguous"), slug=slug, paths=paths, query=slug).strip()
    return found[0].file_text
