"""The plugin's own daily transcript writer. Nothing else needs to be installed.

Every turn is recorded from the two hooks Hermes already calls:

- ``pre_llm_call`` gives the message that started the turn (``user_message``), plus
  ``session_id``, ``turn_id``, ``platform`` and ``sender_id``.
- ``post_llm_call`` gives the reply (``assistant_response``) and the turn's messages
  (``conversation_history``), so tool calls can be listed.

Each hook call appends one line to ``journal/YYYY-MM-DD.jsonl`` in the plugin's data folder.
After each reply, today's Markdown file is rebuilt from that journal off the reply path, with
``**Status:** open``. After midnight the finalizer writes the finished day once more with
``**Status:** final``. A final file is never written again. A file this writer did not make is
never replaced.

Speakers: in a shared multi-user chat Hermes puts ``[Display Name]`` in front of the message
(gateway/run_inbound.py, _prefix_inbound_sender_context). That name is the speaker, but only when
the gateway bound a shared chat type for the turn (HERMES_SESSION_CHAT_TYPE is group, channel or
thread; Hermes never adds the tag in a DM). In a DM, the CLI, or when the chat type is unknown, a
leading ``[...]`` is just part of the message: the speaker is the display name the gateway bound
for the turn (HERMES_SESSION_USER_NAME), and with none (the CLI) ``transcript.user.name``
(default ``User``). The reply is from ``transcript.assistant.name`` (default ``Assistant``).

The wiki lines come from the plugin's own suggestions file (see suggestions.py).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

if __package__:
    from .recall import message_text
    from .storage import data_dir
    from .suggestions import index_for_day, message_keys, suggestions_dir
else:
    from recall import message_text
    from storage import data_dir
    from suggestions import index_for_day, message_keys, suggestions_dir

log = logging.getLogger("nyx-wiki-recall")

WRITER = "nyx-wiki-recall"
MARKER = f"Exporter: {WRITER}"
JOURNAL_VERSION = 1
LOCK_NAME = ".nyx-wiki-recall.lock"
INDEX_NAME = "transcriptIndex.md"
DEFAULT_SKIP_PLATFORMS = "cron, subagent"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]

NEW_MESSAGE_HEAD = "[New message]"
BUNDLE_HEAD = "[Recent channel messages]"
# Hermes adds these as user text. They are not someone speaking.
SKIP_TAG_PREFIXES = ("recent channel messages", "context compaction", "context around the replied-to message")
# Hermes's own turns, which can carry the last sender's name in front (hermes_cli/heartbeat.py,
# tools/process_registry_notifications.py).
SYSTEM_TAGS = (("heartbeat", "Heartbeat"), ("important: background process", "Background process"))

_TAG_RE = re.compile(r"\s*\[([^\[\]\n]{1,80}?(?: \[bot\])?)\]")
_BUNDLE_LINE_RE = re.compile(r"^\[([^\[\]\n:]{1,40}?)(?: \[bot\])?\](?=\s|$)")
_LEAD_TAG_RE = re.compile(r"^\s*\[[^\[\]\n]{1,60}?(?: \[bot\])?\]\s*")
_SPACE_RE = re.compile(r"\s+")
_SECRET_RE = re.compile(
    r"https?://(?:(?:canary|ptb)\.)?discord(?:app)?\.com/api/webhooks/\S+"
    r"|https?://hooks\.slack\.com/services/\S+"
    r"|sk-[A-Za-z0-9_\-]{16,}"
    r"|AIza[0-9A-Za-z_\-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[baprs]-[A-Za-z0-9\-]+"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|hf_[A-Za-z0-9]{20,}"
    r"|\b[MNO][A-Za-z0-9_\-]{23,27}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)"
    r"|Bearer\s+[A-Za-z0-9\-._~+/]+=*",
    re.IGNORECASE,
)
BUNDLE_DEDUPE_SECONDS = 2 * 3600
# A write takes seconds. A lock this old was left by a crash, even when its process id has since
# been reused by another program.
LOCK_STALE_SECONDS = 10 * 60

_thread_lock = threading.Lock()


# --- settings ---------------------------------------------------------------------------------

def settings(questions) -> dict | None:
    """The writer's settings, or None when transcripts are off."""
    if not questions.flag("transcript", True):
        return None
    root = data_dir()
    folder = questions.get("transcript.folder", "").strip()
    journal = questions.get("transcript.journal", "").strip()
    skip = questions.get("transcript.skip.platforms", DEFAULT_SKIP_PLATFORMS)
    suggestions = suggestions_dir(questions)
    return {
        "folder": str(Path(folder).expanduser() if folder else root / "transcripts"),
        "journal": str(Path(journal).expanduser() if journal else root / "journal"),
        "suggestions": str(suggestions) if suggestions else "",
        "user": questions.get("transcript.user.name", "").strip() or "User",
        "assistant": questions.get("transcript.assistant.name", "").strip() or "Assistant",
        "skip_platforms": sorted({p.strip().lower() for p in skip.split(",") if p.strip()}),
        "channel_context": questions.flag("transcript.channel_context", True),
        "tools": questions.flag("transcript.tools", True),
        "aliases": _aliases(questions.lines("transcript.alias")),
    }


def _aliases(lines: list[str]) -> dict[str, str]:
    """transcript.alias = Display Name -> Transcript Name, matched without regard to case."""
    out: dict[str, str] = {}
    for line in lines:
        name, sep, label = line.partition("->")
        if sep and name.strip() and label.strip():
            out[name.strip().lower()] = label.strip()[:40]
    return out


def _alias(cfg: dict, name: str | None) -> str | None:
    if name is None:
        return None
    return (cfg.get("aliases") or {}).get(name.lower(), name)


def daily_name(day: date) -> str:
    """English month abbreviation, whatever the system locale."""
    return f"{MONTHS[day.month - 1]}-{day.day:02d}-{day.year}-transcript.md"


def parse_daily_name(name: str) -> date | None:
    match = re.match(r"^([A-Za-z]+)-(\d{2})-(\d{4})-transcript\.md$", name)
    if not match or match.group(1) not in MONTHS:
        return None
    try:
        return date(int(match.group(3)), MONTHS.index(match.group(1)) + 1, int(match.group(2)))
    except ValueError:
        return None


def redact(text: str) -> str:
    return _SECRET_RE.sub("[redacted]", text or "")


# --- journal ----------------------------------------------------------------------------------

def _skipped_platform(cfg: dict, platform: str) -> bool:
    return (platform or "").strip().lower() in set(cfg.get("skip_platforms") or [])


def _append(cfg: dict, row: dict) -> Path:
    day = datetime.fromtimestamp(row["ts"]).date()
    folder = Path(cfg["journal"])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day.isoformat()}.jsonl"
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _thread_lock:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
    return path


def _session_env():
    """Hermes's reader for the HERMES_SESSION_* values bound for this turn, or None outside Hermes."""
    try:
        # Hermes's plugin guide names this as the way for a plugin to read session identity:
        # "Read session identity with gateway.session_context.get_session_env(...) and the other
        # HERMES_SESSION_* names, not os.environ" (website/docs/developer-guide/plugins/index.md, L1792
        # in Hermes 0.22.5). `hermes plugins validate` lists it as a non-public import (a warning,
        # not a failure). If it ever goes away, this returns None and the plugin falls back to
        # transcript.user.name; nothing else depends on it.
        from gateway.session_context import get_session_env
    except Exception:
        return None
    return get_session_env


def session_identity(platform: str) -> dict:
    """Sender display name, chat name and chat type the gateway bound for this turn, or {}.

    Used only when the bound platform is the hook's platform, so a CLI turn never borrows a gateway
    name. chat_type is Hermes's SessionSource.chat_type: dm, group, channel or thread
    (gateway/session.py; bound in gateway/run.py _set_session_env).
    """
    get_session_env = _session_env()
    if get_session_env is None:
        return {}
    try:
        bound = get_session_env("HERMES_SESSION_PLATFORM", "").strip().lower()
        if not bound or bound != (platform or "").strip().lower():
            return {}
        return {
            "name": get_session_env("HERMES_SESSION_USER_NAME", "").strip()[:40],
            "chat": get_session_env("HERMES_SESSION_CHAT_NAME", "").strip()[:80],
            "chat_type": get_session_env("HERMES_SESSION_CHAT_TYPE", "").strip().lower()[:20],
        }
    except Exception:
        return {}


_TRUE = {"1", "true", "yes", "on"}


def cron_turn(platform: str) -> bool:
    """True for a turn run by Hermes's scheduler.

    The scheduler builds its agent with platform="cron" (cron/scheduler.py, _construct_cron_agent),
    which reaches the hooks as platform (agent/turn_context.py, pre_llm_call). It also binds
    HERMES_CRON_SESSION = "1" for the job (cron/scheduler.py, _CronJobContext.enter), checked too.
    """
    if (platform or "").strip().lower() == "cron":
        return True
    get_session_env = _session_env()
    if get_session_env is None:
        return False
    try:
        return str(get_session_env("HERMES_CRON_SESSION", "") or "").strip().lower() in _TRUE
    except Exception:
        return False


# Chat types Hermes gives a chat that more than one person can write in (gateway/session.py
# SessionSource.chat_type). is_shared_multi_user_session treats everything but "dm" as shared.
def shared_chat(chat_type) -> bool:
    """True when a leading [Name] can be Hermes's sender tag. Unknown or blank is not shared."""
    value = str(chat_type or "").strip().lower()
    return bool(value) and value != "dm"


def record_user(cfg: dict, *, user_message, session_id: str = "", turn_id: str = "", platform: str = "",
                sender_id: str = "", ts: float | None = None, name: str = "", chat: str = "",
                chat_type: str = "") -> bool:
    """Note the message that started a turn. False when the platform is skipped or there are no words."""
    if _skipped_platform(cfg, platform):
        return False
    text = message_text(user_message)
    if not text.strip():
        return False
    _append(cfg, {
        "v": JOURNAL_VERSION, "kind": "user", "ts": float(ts if ts is not None else datetime.now().timestamp()),
        "session": str(session_id or ""), "turn": str(turn_id or ""), "platform": str(platform or ""),
        "sender": str(sender_id or ""), "name": str(name or ""), "chat": str(chat or ""),
        # The chat type the gateway bound (dm, group, channel, thread), "" when none: decides whether
        # a leading [Name] is the speaker.
        "chat_type": str(chat_type or ""), "text": redact(text),
        # Hashes of the words as sent, so the Wiki pages: lines still match after redaction.
        "keys": message_keys(text),
    })
    return True


def _turn_steps(conversation_history, final_response: str, with_tools: bool) -> list[dict]:
    """Assistant text and tool calls after the turn's last user message, then the final reply."""
    history = [m for m in (conversation_history or []) if isinstance(m, dict)]
    start = 0
    for index in range(len(history) - 1, -1, -1):
        if history[index].get("role") == "user":
            start = index + 1
            break
    steps: list[dict] = []
    for message in history[start:]:
        if message.get("role") != "assistant":
            continue
        text = message_text(message.get("content")).strip()
        tools = tool_lines(message.get("tool_calls")) if with_tools else []
        if text or tools:
            step = {"text": redact(text), "tools": tools}
            stamp = message.get("timestamp")  # Hermes stamps each message it appends (agent/message_metadata.py)
            if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
                step["ts"] = float(stamp)
            steps.append(step)
    final = (final_response or "").strip()
    final = redact(final)
    if final and not any(step["text"] == final for step in steps):
        steps.append({"text": final, "tools": []})
    return steps


def record_reply(cfg: dict, *, assistant_response, conversation_history=None, session_id: str = "",
                 turn_id: str = "", platform: str = "", ts: float | None = None) -> bool:
    if _skipped_platform(cfg, platform):
        return False
    steps = _turn_steps(conversation_history, message_text(assistant_response), bool(cfg.get("tools", True)))
    if not steps:
        return False
    _append(cfg, {
        "v": JOURNAL_VERSION, "kind": "assistant", "ts": float(ts if ts is not None else datetime.now().timestamp()),
        "session": str(session_id or ""), "turn": str(turn_id or ""), "platform": str(platform or ""),
        "steps": steps,
    })
    return True


def read_journal(cfg: dict, day: date) -> list[dict]:
    path = Path(cfg["journal"]) / f"{day.isoformat()}.jsonl"
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue  # a line cut short by a crash
        if isinstance(row, dict) and row.get("kind") in ("user", "assistant") and "ts" in row:
            rows.append(row)
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


def _journal_rows(cfg: dict, day: date) -> list[dict]:
    """Rows for day. The neighbouring files are read too: a row is filed by the clock's date when it
    was written, so after a time zone change it can sit in the file next door. render_day keeps only
    the rows that fall on day by the current clock."""
    rows: list[dict] = []
    for other in (day - timedelta(days=1), day, day + timedelta(days=1)):
        rows.extend(read_journal(cfg, other))
    rows.sort(key=lambda row: float(row["ts"]))
    return rows


# --- tool lines -------------------------------------------------------------------------------

# The argument worth showing for common Hermes tools; any other tool uses the first of the defaults.
_TOOL_DETAIL = {
    "search_files": ("pattern",), "grep": ("pattern",),
    "session_search": ("query",), "skill_view": ("name",), "browser_navigate": ("url",),
    "terminal": ("command",), "cmd": ("command",), "run_terminal_command": ("command",),
    "read_file": ("path", "target_file"), "write_file": ("path", "target_file"),
    "patch": ("path", "target_file"), "search_replace": ("path", "target_file"),
    "wiki_read": ("path", "slug"),
}


def summarize_tool_call(name: str, arguments) -> str:
    if not name:
        return "[tool]"
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except (json.JSONDecodeError, TypeError):
        args = {}
    if not isinstance(args, dict):
        args = {}
    detail = ""
    keys = _TOOL_DETAIL.get(name, ("path", "target_file", "command", "pattern", "query", "url", "name"))
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            detail = value
            break
    detail = redact(detail).replace("\n", " ").strip()
    if len(detail) > 80:
        detail = detail[:77] + "..."
    return f"[{name}: {detail}]" if detail else f"[{name}]"


def tool_lines(tool_calls) -> list[str]:
    calls = tool_calls
    if isinstance(calls, str) and calls.strip():
        try:
            calls = json.loads(calls)
        except json.JSONDecodeError:
            calls = None
    lines: list[str] = []
    if isinstance(calls, list):
        for call in calls:
            if not isinstance(call, dict):
                continue
            func = call.get("function") if isinstance(call.get("function"), dict) else call
            lines.append(summarize_tool_call(str(func.get("name") or ""), func.get("arguments")))
    return lines


# --- speakers ---------------------------------------------------------------------------------

def _system_speaker(tag: str) -> str | None:
    low = tag.strip().lower()
    for prefix, label in SYSTEM_TAGS:
        if low.startswith(prefix):
            return label
    return None


def speaker_for(text: str, default: str, shared: bool = True) -> str | None:
    """The speaker of one user message, or None for text Hermes added that nobody said.

    shared is False for a DM or the CLI: Hermes adds no [Name] there, so a leading [draft] or [1]
    is part of what the person wrote and the speaker is default. Hermes's own tags (heartbeat,
    background process, pasted channel context) are recognised either way.
    """
    match = _TAG_RE.match(text or "")
    if not match:
        return default
    tag = match.group(1).strip()
    low = tag.lower()
    if low.startswith(SKIP_TAG_PREFIXES):
        return None
    system = _system_speaker(tag)
    if system:
        return system
    # "[Name] [Heartbeat — ...]": Hermes kept the last sender's name on its own turn.
    rest = (text or "")[match.end():]
    second = _TAG_RE.match(rest)
    if second:
        system = _system_speaker(second.group(1))
        if system:
            return system
    if not shared:
        return default
    name = re.split(r"\s+\|\s+|\s+[—–]\s+", tag, maxsplit=1)[0].strip()
    name = re.sub(r"\s*\[bot\]\s*$", "", name, flags=re.IGNORECASE).strip()
    return (name or default)[:40]


def split_message(text: str) -> tuple[str, str]:
    """(pasted channel context, the new message). Context is empty when Hermes pasted none."""
    raw = text or ""
    index = raw.rfind("\n" + NEW_MESSAGE_HEAD)
    if index >= 0:
        return raw[:index].strip(), raw[index + len(NEW_MESSAGE_HEAD) + 1:].strip()
    if raw.lstrip().startswith(NEW_MESSAGE_HEAD):
        return "", raw.lstrip()[len(NEW_MESSAGE_HEAD):].strip()
    return "", raw.strip()


def context_lines(context: str) -> list[tuple[str, str]]:
    """(speaker, '[Speaker] text') for each tagged line under [Recent channel messages]."""
    if BUNDLE_HEAD not in context:
        return []
    block = context[context.index(BUNDLE_HEAD) + len(BUNDLE_HEAD):]
    out: list[tuple[str, str]] = []
    speaker: str | None = None
    lines: list[str] = []

    def flush() -> None:
        if speaker is not None:
            body = "\n".join(lines).strip()
            if body:
                out.append((speaker, body))

    for line in block.split("\n"):
        match = _BUNDLE_LINE_RE.match(line)
        if match and not match.group(1).lower().startswith("replying to"):
            flush()
            speaker = match.group(1).strip()[:40]
            lines = [line]
        elif speaker is not None:
            lines.append(line)
    flush()
    return out


def _norm(body: str) -> str:
    return _SPACE_RE.sub(" ", _LEAD_TAG_RE.sub("", body or "", count=1)).strip().lower()


class _Seen:
    def __init__(self) -> None:
        self.by_speaker: dict[str, list[tuple[float, str]]] = {}

    def add(self, speaker: str, body: str, ts: float) -> None:
        text = _norm(body)
        if text:
            self.by_speaker.setdefault(speaker, []).append((ts, text))

    def has(self, speaker: str, body: str, ts: float) -> bool:
        text = _norm(body)
        if not text:
            return True
        for other_ts, other in self.by_speaker.get(speaker, ()):
            if abs(other_ts - ts) > BUNDLE_DEDUPE_SECONDS:
                continue
            if other == text or (len(text) >= 20 and text in other):
                return True
        return False


# --- wiki lines -------------------------------------------------------------------------------

def wiki_lines(texts: list[str], index: dict[str, dict], keys: list[str] | None = None) -> str:
    """The Wiki pages: block for a user turn, or "" when the plugin never looked at it."""
    row = None
    for key in keys or []:
        row = index.get(key)
        if row is not None:
            break
    for text in texts if row is None else []:
        for key in message_keys(text):
            row = index.get(key)
            if row is not None:
                break
        if row is not None:
            break
    if row is None:
        return ""
    if not row.get("asked"):
        return "Wiki pages:\nineligible"
    pages = row.get("pages") if isinstance(row.get("pages"), list) else []
    lines = ["Wiki pages:"]
    for page in pages:
        if not isinstance(page, dict) or not str(page.get("path") or "").strip():
            continue
        try:
            score = float(page.get("score"))
        except (TypeError, ValueError):
            score = 0.0
        lines.append(f"{len(lines)}. {str(page['path']).strip()} ({score:.2f})")
    if len(lines) == 1:
        # error: the decision model timed out or failed. none: it scored, nothing fit.
        lines.append("not scored" if row.get("error") else "none")
    return "\n".join(lines)


# --- render -----------------------------------------------------------------------------------

def _where(row: dict, chats: dict[str, str]) -> str:
    platform = (row.get("platform") or "").strip() or "unknown"
    label = platform[:1].upper() + platform[1:]
    session = (row.get("session") or "").strip()
    chat = (row.get("chat") or "").strip() or chats.get(session, "")
    if chat:
        return f"{label} / {chat}"
    return f"{label} (session {session})" if session else label


def _row_shared(row: dict) -> bool:
    """Whether this user row came from a shared chat. A row written before chat types were noted
    (no chat_type key) keeps the earlier rule, which read a leading [Name] everywhere."""
    if "chat_type" not in row:
        return True
    return shared_chat(row.get("chat_type"))


def _items(rows: list[dict], day: date, cfg: dict) -> tuple[list[dict], set[str]]:
    seen_turns: set[tuple] = set()
    kept: list[dict] = []
    sessions: set[str] = set()
    plain = _Seen()
    parsed: list[tuple[dict, str, str, str | None]] = []
    for row in rows:
        ts = float(row["ts"])
        if datetime.fromtimestamp(ts).date() != day:
            continue
        if row["kind"] == "user":
            context, new = split_message(str(row.get("text") or ""))
            who = _alias(cfg, speaker_for(new, str(row.get("name") or "").strip() or cfg["user"], _row_shared(row)))
            parsed.append((row, context, new, who))
            if who is not None:
                plain.add(who, new, ts)
        else:
            parsed.append((row, "", "", None))
    # A reply row has no chat name of its own; it takes its session's.
    chats = {str(r.get("session") or ""): str(r.get("chat") or "") for r in rows if r.get("chat")}
    from_context = _Seen()
    for row, context, new, who in parsed:
        ts = float(row["ts"])
        where = _where(row, chats)
        key = (row["kind"], row.get("session"), row.get("turn") or f"ts:{ts:.3f}")
        if key in seen_turns:
            continue  # the same turn noted twice (a retried hook)
        seen_turns.add(key)
        if row["kind"] == "user":
            added = False
            if cfg.get("channel_context") and context:
                for speaker, body in context_lines(context):
                    speaker = _alias(cfg, speaker)
                    if plain.has(speaker, body, ts) or from_context.has(speaker, body, ts):
                        continue
                    from_context.add(speaker, body, ts)
                    kept.append({"ts": ts, "speaker": speaker, "body": redact(body), "tools": [],
                                 "wiki": [], "where": where})
                    added = True
            if who is not None and new:
                kept.append({"ts": ts, "speaker": who, "body": redact(new), "tools": [],
                             "wiki": [str(row.get("text") or ""), new], "keys": list(row.get("keys") or []),
                             "where": where})
                added = True
            if added:
                sessions.add(str(row.get("session") or ""))
            continue
        for step in row.get("steps") or []:
            body = redact(str(step.get("text") or "").strip())
            tools = [redact(str(line)) for line in step.get("tools") or []]
            if not body and not tools:
                continue
            step_ts = step.get("ts")
            step_ts = float(step_ts) if isinstance(step_ts, (int, float)) and abs(float(step_ts) - ts) < 86400 else ts
            kept.append({"ts": step_ts, "speaker": cfg["assistant"], "body": body, "tools": tools,
                         "wiki": [], "where": where})
            sessions.add(str(row.get("session") or ""))
    return kept, sessions


def render_day(day: date, rows: list[dict], cfg: dict, *, status: str, exported_at: datetime,
               index: dict[str, dict] | None = None) -> str | None:
    """Markdown for one local day, or None when nothing was said that day."""
    index = index or {}
    kept, sessions = _items(rows, day, cfg)
    if not kept:
        return None
    lines: list[str] = []
    current = None
    for item in kept:
        if item["where"] != current:
            if lines:
                lines.append("")
            lines.extend([f"## {item['where']}", ""])
            current = item["where"]
        stamp = datetime.fromtimestamp(item["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        lines.extend([f"### {item['speaker']} ({stamp})", ""])
        if item["body"]:
            lines.append(item["body"])
            section = wiki_lines(item["wiki"], index, item.get("keys")) if item["wiki"] else ""
            if section:
                lines.extend(["", section])
            lines.append("")
        for tool in item["tools"]:
            lines.append("  " + tool)
        if item["tools"]:
            lines.append("")
    body = "\n".join(lines).rstrip() + "\n"
    body_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    platforms = sorted({(row.get("platform") or "unknown") for row in rows
                        if datetime.fromtimestamp(float(row["ts"])).date() == day})
    header = "\n".join([
        f"# {MONTH_NAMES[day.month - 1]} {day.day}, {day.year}",
        "",
        f"**Status:** {status}",
        f"**Messages:** {len(kept)}",
        f"**Sessions:** {len(sessions)}",
        f"**Exported:** {exported_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**{MARKER}**",
        f"**Body-SHA256:** {body_sha}",
        f"**Source:** Hermes turns recorded by {WRITER} ({', '.join(platforms)})",
        "",
        "---",
        "",
    ])
    return header + body


def owned_meta(head: str) -> dict | None:
    """Status and body hash of a file this writer made. None for anyone else's file."""
    head = head[:2500]
    if f"**{MARKER}**" not in head:
        return None
    status = re.search(r"\*\*Status:\*\* (\w+)", head)
    sha = re.search(r"\*\*Body-SHA256:\*\* ([0-9a-f]+)", head)
    return {"status": status.group(1) if status else "final", "body_sha": sha.group(1) if sha else ""}


def file_status(path: Path) -> str:
    """final, open, foreign (someone else's file), or missing."""
    if not path.is_file():
        return "missing"
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        meta = owned_meta(handle.read(2500))
    return "foreign" if meta is None else meta["status"]


# --- locking ----------------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)  # signal 0 only checks; never used on Windows, where it would kill
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire(path: Path) -> bool:
    """One writer per folder across processes (a CLI and a gateway can share a Hermes home)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            old = int(path.read_text(encoding="utf-8").strip().split()[0])
        except (OSError, ValueError, IndexError):
            old = 0
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            age = 0.0
        if old != os.getpid() and _pid_alive(old) and age < LOCK_STALE_SECONDS:
            return False
        try:
            path.unlink()
        except OSError:
            return False
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)
    return True


def _release(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8").strip().split()
        if text and int(text[0]) == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass


_write_lock = threading.Lock()


# --- write ------------------------------------------------------------------------------------

def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows refuses while another program has the file open (an editor, a reader).
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def write_day(cfg: dict, day: date, *, status: str, now: datetime | None = None) -> str:
    """wrote, unchanged, empty, final (already final, left alone), foreign (left alone), or busy."""
    now = now or datetime.now()
    folder = Path(cfg["folder"])
    path = folder / daily_name(day)
    if not _write_lock.acquire(timeout=30):
        return "busy"
    try:
        lock = folder / LOCK_NAME
        if not _acquire(lock):
            return "busy"
        try:
            existing = file_status(path)
            if existing == "foreign":
                return "foreign"
            if existing == "final":
                return "final"
            index = index_for_day(cfg["suggestions"], day) if cfg.get("suggestions") else {}
            rendered = render_day(day, _journal_rows(cfg, day), cfg, status=status, exported_at=now, index=index)
            if rendered is None:
                return "empty"
            if existing == "open":
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    old = owned_meta(handle.read(2500)) or {}
                new = owned_meta(rendered) or {}
                if old.get("status") == status and old.get("body_sha") == new.get("body_sha"):
                    return "unchanged"
            folder.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, rendered)
            refresh_index(folder)
            return "wrote"
        finally:
            _release(lock)
    finally:
        _write_lock.release()


def refresh_today(cfg: dict, now: datetime | None = None) -> str:
    now = now or datetime.now()
    return write_day(cfg, now.date(), status="open", now=now)


def refresh_index(folder: Path) -> None:
    """transcriptIndex.md: one row per daily file in the folder. Summaries someone wrote are kept."""
    index_path = folder / INDEX_NAME
    previous: dict[str, str] = {}
    if index_path.is_file():
        for line in index_path.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = [part.strip() for part in line.strip().strip("|").split("|")]
            if len(parts) >= 3 and re.match(r"\d{4}-\d{2}-\d{2}$", parts[0]):
                previous[parts[0]] = parts[2]
    rows = sorted((d, p) for p in folder.glob("*-transcript.md") if (d := parse_daily_name(p.name)))
    lines = ["# Transcript Index", "",
             "*One row per daily file in this folder. Open the file for the words; this page is only the catalog.*",
             "", "| Date | Daily File | Summary |", "|------|------------|---------|"]
    for day, path in rows:
        summary = previous.get(day.isoformat(), "")
        if not summary or summary.endswith(" messages."):
            try:
                head = path.read_text(encoding="utf-8", errors="replace")[:1500]
            except OSError:
                head = ""
            match = re.search(r"\*\*Messages:\*\* (\d+)", head)
            status = re.search(r"\*\*Status:\*\* (\w+)", head)
            if match and f"**{MARKER}**" in head:
                summary = f"{(status.group(1) if status else 'final').capitalize()}, {match.group(1)} messages."
            summary = summary or "Daily conversation transcript."
        lines.append(f"| {day.isoformat()} | `{path.name}` | {summary} |")
    lines.extend(["", "---", "", f"*Rebuilt by {WRITER} when it writes a daily file.*", ""])
    _atomic_write(index_path, "\n".join(lines))
