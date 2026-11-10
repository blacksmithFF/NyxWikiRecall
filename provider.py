"""Hermes memory provider. Hermes lists it when the plugin is installed.

Hermes uses the provider only after two things are true: questions.txt says
provider = yes, and Hermes's own memory setting names nyx-wiki-recall. The
person running Hermes chooses that setting (hermes memory setup, or
memory.provider in config.yaml); the plugin never writes it. The gateway reads
the setting when it starts.

When the provider has no hand-off from the hook, it reads the last few messages
of the session from Hermes's state.db, read-only, to give the decision model
the same conversation the hook would have seen.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

log = logging.getLogger("nyx-wiki-recall")

try:
    from agent.memory_provider import MemoryProvider, RecallStatus
except ImportError:  # tests that import the module without the Hermes tree
    class MemoryProvider:  # type: ignore[no-redef]
        pass

    RecallStatus = None  # type: ignore[assignment]

if __package__:
    from . import jev_client
    from .bridge import provider_down, provider_up, take
    from .transcript import cron_turn
    from .questions import load
    from .recall import attach
    from .suggestions import record_turn
    from .wiki_tree import load_wiki
else:
    import jev_client
    from bridge import provider_down, provider_up, take
    from transcript import cron_turn
    from questions import load
    from recall import attach
    from suggestions import record_turn
    from wiki_tree import load_wiki

_DEFAULT_URL = "https://openrouter.ai/api/alpha/decisions"
_DEFAULT_MODEL = "typesafe/jev-1.13"


def recent_messages(db_path: Path, session_id: str, limit: int = 40) -> list[dict]:
    """Earlier messages saved for this session. Empty when the database is missing."""
    if not session_id or not db_path.is_file():
        return []
    uri = db_path.resolve().as_posix()
    try:
        connection = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}
        if "content" not in columns or "role" not in columns:
            return []
        api = ", api_content" if "api_content" in columns else ""
        rows = connection.execute(
            f"SELECT role, content{api} FROM messages "
            "WHERE session_id = ? AND role IN ('user', 'assistant') "
            "ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    history: list[dict] = []
    for row in reversed(rows):
        item = {"role": row[0] or "", "content": row[1] or ""}
        if len(row) > 2 and row[2]:
            item["api_content"] = row[2]
        history.append(item)
    return history


class WikiRecallProvider(MemoryProvider):
    """One instance per Hermes agent. The hook and this provider share bridge.py."""

    def __init__(self, questions_path: str = "", wiki_path: str = "") -> None:
        self._questions_path_override = questions_path
        self._wiki_path_override = wiki_path
        self._session_id = ""
        self._hermes_home = ""
        self._platform = ""
        self._last_count: int | None = None
        self._owner = object()

    @property
    def name(self) -> str:
        return "nyx-wiki-recall"

    def _questions_path(self) -> Path:
        if self._questions_path_override:
            return Path(self._questions_path_override).expanduser()
        settings = _plugin_settings()
        configured = str(settings.get("questions_path") or "").strip()
        if configured:
            return Path(configured).expanduser()
        return Path(__file__).resolve().parent / "questions.txt"

    def _wiki_path(self) -> str:
        if self._wiki_path_override:
            return self._wiki_path_override
        return str(_plugin_settings().get("wiki_path") or "")

    def _flag_on(self) -> bool:
        path = self._questions_path()
        if not path.is_file():
            return False
        try:
            return load(path).flag("provider")
        except Exception:  # unreadable or not UTF-8: report unavailable, never raise into Hermes
            return False

    def is_available(self) -> bool:
        return self._flag_on()

    def unavailable_reason(self) -> str:
        if self._flag_on():
            return ""
        if _hook_enabled() is False:
            # `hermes plugins install --enable` selects this plugin as memory.provider without
            # listing it in plugins.enabled, so the hook (transcripts and glimpses) never loads.
            return (
                "questions.txt has provider = no, and the nyx-wiki-recall hook is not enabled, so no "
                "transcripts are written and no glimpses are shown. Run `hermes plugins enable "
                "nyx-wiki-recall`, then set memory.provider back to the provider you used before "
                "(or leave it empty)."
            )
        return (
            "questions.txt has provider = no. Set provider = yes there to use NyxWikiRecall as the "
            "memory provider; until then its glimpses come from the pre_llm_call hook."
        )

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id or ""
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        # Hermes passes the agent's platform ("cron" for the scheduler) and agent_context
        # (agent/agent_init.py, _memory_provider_init_kwargs).
        context = str(kwargs.get("agent_context") or "").strip().lower()
        self._platform = "cron" if context == "cron" else str(kwargs.get("platform") or "")
        if self._flag_on():
            provider_up(self._owner)
            _warn_once_hook_off()
        else:
            provider_down(self._owner)

    def shutdown(self) -> None:
        provider_down(self._owner)

    def get_tool_schemas(self) -> list[dict]:
        return []

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        self._last_count = None
        if not self._flag_on():
            return ""
        hit = take(session_id or self._session_id, query)
        if hit is not None:
            text, count = hit
            self._last_count = count if text and count else None
            return text
        return self._lookup(query, session_id or self._session_id)

    def recall_status(self):
        if RecallStatus is None or not self._last_count:
            return None
        return RecallStatus(provider_label="NyxWikiRecall", count=self._last_count)

    def _lookup(self, query: str, session_id: str) -> str:
        path = self._questions_path()
        if not path.is_file():
            return ""
        questions = load(path)
        if not questions.flag("lookups.on_cron", False) and cron_turn(self._platform):
            return ""
        key = jev_client.api_key()
        if not key:
            return ""  # no lookup and no Wiki pages: row; the plugin warned once at load
        wiki = load_wiki(self._wiki_path(), questions)
        report: dict = {}

        def ask(message, items, timeout):
            if __package__:
                from .jev_client import ask as jev_ask
            else:
                from jev_client import ask as jev_ask
            return jev_ask(
                message,
                items,
                timeout,
                model=questions.get("model", _DEFAULT_MODEL),
                url=questions.get("decisions.url", _DEFAULT_URL),
                key=key,
            )

        db_path = Path(self._hermes_home) / "state.db" if self._hermes_home else Path()
        history = recent_messages(db_path, session_id) if self._hermes_home else []
        text = attach(query, history, wiki, questions, ask, report=report)
        try:
            record_turn(questions, query, report)
        except Exception as exc:
            log.warning("nyx-wiki-recall: transcript note was not saved (%s)", type(exc).__name__)
        pages = report.get("pages") or []
        self._last_count = len(pages) if text and pages else None
        return text


_hook_off_warned = False


def _warn_once_hook_off() -> None:
    """Provider on but hook off: glimpses still come from the provider, transcripts do not."""
    global _hook_off_warned
    if _hook_off_warned or _hook_enabled() is not False:
        return
    _hook_off_warned = True
    log.warning(
        "nyx-wiki-recall: the memory provider is on but the hook is not enabled, so no transcripts "
        "are written. Run `hermes plugins enable nyx-wiki-recall` to turn transcripts on."
    )


def _hermes_config() -> dict | None:
    """Hermes's config, read-only. None when it cannot be read."""
    try:
        from hermes_cli.config import load_config

        config = load_config()
    except Exception:
        return None
    return config if isinstance(config, dict) else None


def _hook_enabled() -> bool | None:
    """Whether plugins.enabled lists this plugin. None when Hermes's config cannot be read."""
    config = _hermes_config()
    if config is None:
        return None
    plugins = config.get("plugins")
    enabled = plugins.get("enabled") if isinstance(plugins, dict) else None
    if not isinstance(enabled, list):
        return False
    return "nyx-wiki-recall" in {str(name).strip() for name in enabled}


def _plugin_settings() -> dict:
    config = _hermes_config()
    if config is None:
        return {}
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        return {}
    entries = plugins.get("entries")
    if not isinstance(entries, dict):
        return {}
    entry = entries.get("nyx-wiki-recall")
    if not isinstance(entry, dict):
        return {}
    settings = entry.get("settings")
    return settings if isinstance(settings, dict) else {}
