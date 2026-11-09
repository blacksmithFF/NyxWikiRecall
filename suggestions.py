"""Remember which wiki pages a turn suggested, without storing the message."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

if __package__:
    from .bridge import shared
else:
    from bridge import shared

log = logging.getLogger("nyx-wiki-recall")

# With no post-turn hook, wait this long so Hermes has saved the message first.
_LATE_SECONDS = 20.0

_SPACE = re.compile(r"\s+")
_LEAD_TAG = re.compile(r"^\[[^\]\n]{1,80}\]\s*")
_NEW_MESSAGE = "[New message]"


def _digest(text: str) -> str:
    collapsed = _SPACE.sub(" ", (text or "").replace("\r\n", "\n").replace("\r", "\n").strip())
    if not collapsed:
        return ""
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def message_keys(text: str) -> list[str]:
    """Hashes a transcript can match. The message itself is not returned."""
    raw = text or ""
    keys: list[str] = []

    def add(part: str) -> None:
        digest = _digest(part)
        if digest and digest not in keys:
            keys.append(digest)

    add(raw)
    index = raw.rfind(_NEW_MESSAGE)
    if index >= 0:
        add(raw[index + len(_NEW_MESSAGE):])
    stripped = _LEAD_TAG.sub("", raw.strip(), count=1)
    if stripped != raw.strip():
        add(stripped)
    return keys


def append_record(path: Path, message: str, pages: list[dict], *, asked: bool, error: str = "") -> None:
    """Append one lookup. Latest line wins for the same message.

    error set means JEV was asked but timed out or failed: the transcript shows "not scored".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    clean_pages = []
    if asked:
        for page in pages:
            if not isinstance(page, dict):
                continue
            rel = str(page.get("path") or "").strip()
            if not rel:
                continue
            try:
                score = float(page.get("score"))
            except (TypeError, ValueError):
                score = 0.0
            clean_pages.append({"path": rel, "score": score})
    row = {"keys": message_keys(message), "asked": bool(asked), "pages": clean_pages}
    if error:
        row["error"] = str(error)[:200]
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_index(path: Path) -> dict[str, dict]:
    """Map each message hash to the latest matching lookup in one file."""
    index: dict[str, dict] = {}
    _read_into(index, path)
    return index


def _read_into(index: dict[str, dict], path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line cut short by a crash
        if not isinstance(row, dict) or "asked" not in row:
            continue
        for key in row.get("keys") or []:
            if isinstance(key, str) and key:
                index[key] = row


def day_file(folder, day: date) -> Path:
    """suggestions/YYYY-MM-DD.jsonl: the lookups made on that local day."""
    return Path(folder) / f"{day.isoformat()}.jsonl"


def load_days(folder, days) -> dict[str, dict]:
    """Map each message hash to the latest lookup in the files for these days, read oldest first."""
    index: dict[str, dict] = {}
    if not folder:
        return index
    for day in sorted(set(days)):
        _read_into(index, day_file(folder, day))
    return index


def index_for_day(folder, day: date) -> dict[str, dict]:
    """The lookups a transcript for day can need: that day's file and the two next to it.

    A lookup is filed by the clock's date when it finished. It runs after the message is noted, so a
    message just before midnight can have its lookup in the next day's file, and after a time zone
    change a lookup can sit in the file before. Older days are never read.
    """
    return load_days(folder, (day - timedelta(days=1), day, day + timedelta(days=1)))


_OFF = {"off", "no", "none", "false", "0"}
LEGACY_NAME = "wiki_suggestions.jsonl"


def suggestions_dir(questions) -> Path | None:
    """suggestions.folder, or suggestions/ in the plugin's data folder when blank. None when off.

    The older single-file setting suggestions.file = off still turns the file off.
    """
    if __package__:
        from .storage import configured_path
    else:
        from storage import configured_path
    if questions.get("suggestions.file", "").strip().lower() in _OFF:
        return None
    return configured_path(questions, "suggestions.folder", "suggestions")


def note_legacy_file() -> str:
    """One log line when a wiki_suggestions.jsonl from an older version is still in the data folder.

    That file held every lookup in one file, with no dates, so it cannot be split by day. It is no
    longer read or written, and it is never deleted by the plugin.
    """
    if __package__:
        from .storage import data_dir
    else:
        from storage import data_dir
    try:
        legacy = data_dir() / LEGACY_NAME
        if not legacy.is_file():
            return ""
    except OSError:
        return ""
    state = shared()
    with state.lock:
        if getattr(state, "legacy_noted", False):
            return ""
        state.legacy_noted = True
    message = (
        "nyx-wiki-recall: %s from an older version is no longer read; lookups now go to one file per day "
        "in suggestions/. Transcripts already written are unchanged. You can delete the old file." % LEGACY_NAME
    )
    log.info(message)
    return message


def refresh_target(questions):
    """The built-in writer's settings (a dict) that rewrite today's transcript, or None when transcripts are off."""
    if __package__:
        from .transcript import settings as transcript_settings
    else:
        from transcript import settings as transcript_settings
    return transcript_settings(questions)


def record_turn(questions, message: str, report: dict, *, now: datetime | None = None) -> None:
    """Write the scored pages, or ineligible, then ask for today's transcript to be refreshed.

    This runs before Hermes saves the message, so the refresh waits: the post-turn hook runs it,
    or, with no post-turn hook, a background refresh a few seconds later. The reply never waits.
    """
    error = "" if report.get("ineligible") else str(report.get("error") or "")
    asked = bool(report.get("asked")) or bool(error)
    ineligible = bool(report.get("ineligible"))
    if not asked and not ineligible:
        return
    folder = suggestions_dir(questions)
    if folder is not None:
        append_record(
            day_file(folder, (now or datetime.now()).date()),
            message,
            list(report.get("pages") or []) if asked else [],
            asked=asked,
            error=error,
        )
    target = refresh_target(questions)
    if target is None:
        return
    state = shared()
    with state.lock:
        if state.post_hook_live:
            state.pending_script = target
            return
    request_refresh(target, delay=_LATE_SECONDS)


def flush_pending() -> bool:
    """After Hermes saved the turn: refresh today's transcript in the background. True when one was asked for."""
    state = shared()
    with state.lock:
        target = state.pending_script
        state.pending_script = None
    if target is None:
        return False
    request_refresh(target)
    return True


def drop_pending() -> None:
    """The caller refreshes today's transcript itself, so a refresh noted by the lookup is not needed."""
    state = shared()
    with state.lock:
        state.pending_script = None


def request_refresh(target, *, delay: float = 0.0) -> None:
    """Refresh on a background thread. Requests that arrive while one waits or runs are folded in."""
    state = shared()
    with state.lock:
        state.refresh_wanted = target
        state.refresh_due = time.monotonic() + max(0.0, delay)
        worker = state.refresh_worker
        if worker is not None and worker.is_alive():
            return
        worker = threading.Thread(target=_drain, name="nyx-wiki-recall-transcript", daemon=True)
        state.refresh_worker = worker
    worker.start()


def wait_for_refresh(timeout: float = 30.0) -> bool:
    """Block until queued refreshes finish. For tests and scripts."""
    worker = shared().refresh_worker
    if worker is not None:
        worker.join(timeout)
        return not worker.is_alive()
    return True


def _drain() -> None:
    state = shared()
    while True:
        with state.lock:
            target = state.refresh_wanted
            if target is None:
                state.refresh_worker = None
                return
            wait = state.refresh_due - time.monotonic()
            if wait <= 0:
                state.refresh_wanted = None
        if wait > 0:
            time.sleep(min(wait, 1.0))
            continue
        try:
            refresh_transcript(target)
        except Exception as exc:
            log.warning("nyx-wiki-recall: transcript refresh failed (%s)", type(exc).__name__)


def refresh_transcript(target) -> None:
    """Rewrite today's transcript with the built-in writer (target is its settings)."""
    if not isinstance(target, dict):
        return
    if __package__:
        from .transcript import refresh_today
    else:
        from transcript import refresh_today
    refresh_today(target)


def pages_for(index: dict[str, dict], message: str) -> list[dict] | None:
    """The pages for this message, or None when this message was not scored."""
    for key in message_keys(message):
        row = index.get(key)
        if row is not None:
            pages = row.get("pages")
            return list(pages) if isinstance(pages, list) else []
    return None
