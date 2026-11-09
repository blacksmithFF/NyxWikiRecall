"""NyxWikiRecall: ask JEV which wiki glimpses fit this message, then attach those glimpses.

The same two hooks also record every turn, so the plugin writes daily transcripts by itself
(transcript.py) and finishes each day after midnight (finalize.py).
"""

from __future__ import annotations

import logging
from pathlib import Path

if __package__:
    from .bridge import ready, shared, store
    from .finalize import start as start_finalizer
    from . import jev_client
    from .jev_client import NO_KEY_WARNING, ask as jev_ask
    from .provider import WikiRecallProvider
    from .questions import load, parse
    from .recall import attach, message_text, read_article
    from .suggestions import drop_pending, flush_pending, note_legacy_file, record_turn, request_refresh
    from . import transcript
    from .wiki_tree import load_wiki
else:
    from bridge import ready, shared, store
    from finalize import start as start_finalizer
    import jev_client
    from jev_client import NO_KEY_WARNING, ask as jev_ask
    from provider import WikiRecallProvider
    from questions import load, parse
    from recall import attach, message_text, read_article
    from suggestions import drop_pending, flush_pending, note_legacy_file, record_turn, request_refresh
    import transcript
    from wiki_tree import load_wiki

log = logging.getLogger("nyx-wiki-recall")

_DEFAULT_URL = "https://openrouter.ai/api/alpha/decisions"
_DEFAULT_MODEL = "typesafe/jev-1.13"
_seen_mtime: int | None = None
PROVIDER_NAME = "nyx-wiki-recall"


def _questions_path(ctx) -> Path:
    override = ""
    try:
        override = ctx.get_config("questions_path", "") or ""
    except Exception:
        override = ""
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent / "questions.txt"


def _runtime(ctx):
    path = _questions_path(ctx)
    if not path.is_file():
        log.warning("nyx-wiki-recall: questions file missing (%s)", path.name)
        return None, None
    questions = load(path)
    wiki_path = ""
    try:
        wiki_path = ctx.get_config("wiki_path", "") or ""
    except Exception:
        wiki_path = ""
    return questions, load_wiki(wiki_path, questions)


def _api_key() -> str:
    return jev_client.api_key()


def _warn_once_without_key() -> str:
    """At load: one warning when there is no OpenRouter key. Not repeated per message or per reload."""
    if _api_key():
        return ""
    state = shared()
    with state.lock:
        if getattr(state, "no_key_warned", False):
            return ""
        state.no_key_warned = True
    log.warning(NO_KEY_WARNING)
    return NO_KEY_WARNING


def lookups_on_cron(questions) -> bool:
    """lookups.on_cron: look the wiki up on scheduled (cron) turns too. Off by default."""
    return questions.flag("lookups.on_cron", False)


def _warn_script_setting(questions) -> None:
    """transcript.script was removed. Say so once when an older questions.txt still sets it."""
    if questions is None or not questions.get("transcript.script", "").strip():
        return
    state = shared()
    with state.lock:
        if getattr(state, "script_warned", False):
            return
        state.script_warned = True
    log.warning("nyx-wiki-recall: transcript.script is no longer supported and is ignored; "
                "the built-in transcript writer is used.")


def _provider_setting() -> str | None:
    """Hermes's memory.provider, read only. None when Hermes's config cannot be read."""
    try:
        from hermes_cli.config import load_config_readonly as _load
    except ImportError:
        try:
            from hermes_cli.config import load_config as _load
        except ImportError:
            return None
    try:
        config = _load()
    except Exception:
        return None
    memory = config.get("memory") if isinstance(config, dict) else None
    if not isinstance(memory, dict):
        return ""
    return str(memory.get("provider") or "").strip()


def _check_provider_setting(questions, read_setting=None) -> str:
    """Warn when questions.txt's provider flag and Hermes's memory.provider disagree.

    The plugin never writes Hermes's settings. The person running Hermes picks the memory
    provider (hermes memory setup, or memory.provider in config.yaml). Until both say
    nyx-wiki-recall, glimpses stay on the pre_llm_call hook. Returns the warning, or "".
    """
    global _seen_mtime
    stamp = 0
    if questions.path:
        try:
            stamp = Path(questions.path).stat().st_mtime_ns
        except OSError:
            stamp = 0
    if _seen_mtime is not None and stamp == _seen_mtime:
        return ""
    _seen_mtime = stamp
    current = (read_setting or _provider_setting)()
    if current is None:
        return ""
    want = questions.flag("provider")
    message = ""
    if want and current != PROVIDER_NAME:
        message = (
            "nyx-wiki-recall: questions.txt has provider = yes, but Hermes's memory.provider is %r. "
            "Glimpses stay on the hook. To use the provider, choose it in Hermes "
            "(hermes memory setup, or memory.provider: nyx-wiki-recall in config.yaml) and restart Hermes."
            % (current or "built-in")
        )
    elif not want and current == PROVIDER_NAME:
        message = (
            "nyx-wiki-recall: Hermes's memory.provider is nyx-wiki-recall, but questions.txt has provider = no. "
            "The provider reports itself unavailable and glimpses stay on the hook. Set provider = yes, "
            "or choose another memory provider in Hermes."
        )
    if message:
        log.warning(message)
    return message


def _transcript_settings(questions):
    try:
        return transcript.settings(questions if questions is not None else parse(""))
    except Exception as exc:
        log.warning("nyx-wiki-recall: transcript settings unreadable (%s)", type(exc).__name__)
        return None


def _ask_for(questions):
    def ask(message, items, timeout):
        return jev_ask(
            message,
            items,
            timeout,
            model=questions.get("model", _DEFAULT_MODEL),
            url=questions.get("decisions.url", _DEFAULT_URL),
            key=_api_key(),
        )

    return ask


def _lookup(user_message, conversation_history, questions, wiki, session_id: str, *, hand_to_provider: bool):
    if not _api_key():
        # No key: no lookup, no block, no Wiki pages: row. register() already said why, once.
        if hand_to_provider:
            store(session_id, message_text(user_message).strip(), "", None)
        return None
    report: dict = {}
    text = attach(
        user_message,
        conversation_history,
        wiki,
        questions,
        _ask_for(questions),
        report=report,
    )
    try:
        record_turn(questions, message_text(user_message), report)
    except Exception as exc:
        log.warning("nyx-wiki-recall: transcript note was not saved (%s)", type(exc).__name__)
    if hand_to_provider:
        pages = report.get("pages") or []
        count = len(pages) if text and pages else None
        store(session_id, message_text(user_message).strip(), text, count)
        return None
    if not text:
        return None
    return {"context": text}


def register(ctx) -> None:
    # Hermes loads this twice. The memory-provider load uses _ProviderCollector.
    # Register the provider there and return, so the hook is not registered twice.
    # register_memory_provider is the name Hermes looks for in this file.
    if type(ctx).__name__ == "_ProviderCollector":
        ctx.register_memory_provider(WikiRecallProvider())
        return

    def pre_llm_call(user_message=None, conversation_history=None, session_id="", **kwargs):
        try:
            questions, wiki = _runtime(ctx)
        except Exception as exc:
            log.warning("nyx-wiki-recall: settings unreadable (%s)", type(exc).__name__)
            questions, wiki = None, None
        # Record the turn first, so a failed lookup still leaves it in the transcript.
        writer = _transcript_settings(questions)
        if writer is not None:
            try:
                platform = str(kwargs.get("platform") or "")
                transcript.record_user(
                    writer,
                    user_message=user_message,
                    session_id=str(session_id or ""),
                    turn_id=str(kwargs.get("turn_id") or ""),
                    platform=platform,
                    sender_id=str(kwargs.get("sender_id") or ""),
                    **transcript.session_identity(platform),
                )
            except Exception as exc:
                log.warning("nyx-wiki-recall: turn was not recorded (%s)", type(exc).__name__)
        try:
            if questions is None:
                return None
            _check_provider_setting(questions)
            # ready() is true only after Hermes itself loaded this plugin as its memory provider
            # (memory.provider names it and provider = yes): agent/agent_init.py.
            hand_to_provider = questions.flag("provider") and ready()
            platform = str(kwargs.get("platform") or "")
            if not lookups_on_cron(questions) and transcript.cron_turn(platform):
                # Scheduled jobs get no wiki lookup unless lookups.on_cron = yes.
                if hand_to_provider:
                    store(str(session_id or ""), message_text(user_message).strip(), "", None)
                return None
            return _lookup(
                user_message,
                conversation_history,
                questions,
                wiki,
                str(session_id or ""),
                hand_to_provider=hand_to_provider,
            )
        except Exception as exc:
            log.warning("nyx-wiki-recall: lookup failed (%s)", type(exc).__name__)
            return None

    def wiki_read(args, **kwargs):
        del kwargs
        payload = args if isinstance(args, dict) else {}
        slug = str(payload.get("slug") or "")
        path = str(payload.get("path") or "")
        try:
            questions, wiki = _runtime(ctx)
            if questions is None:
                return "The wiki is unavailable: the plugin's questions file is missing."
            if wiki is None:
                return questions.block("nyx.unconfigured").strip()
            return read_article(wiki, questions, slug=slug, path=path)
        except Exception as exc:
            log.warning("nyx-wiki-recall: wiki_read failed (%s)", type(exc).__name__)
            return "The wiki page could not be read."

    try:
        ctx.register_memory_provider(WikiRecallProvider())
    except Exception as exc:
        log.warning("nyx-wiki-recall: memory provider was not listed (%s)", type(exc).__name__)

    # An unreadable questions file must not stop the plugin loading: transcripts still run.
    try:
        questions, _wiki = _runtime(ctx)
        if questions is not None:
            _check_provider_setting(questions)
            _warn_script_setting(questions)
    except Exception as exc:
        log.warning("nyx-wiki-recall: settings unreadable (%s)", type(exc).__name__)
        questions = None
    try:
        _warn_once_without_key()
        note_legacy_file()
    except Exception as exc:
        log.warning("nyx-wiki-recall: start-up checks failed (%s)", type(exc).__name__)
    description = "Read the full wiki article behind a glimpse. Pass slug or path."
    if questions is not None and questions.block("nyx.tool_description").strip():
        description = questions.block("nyx.tool_description").strip()

    def post_llm_call(session_id="", assistant_response=None, conversation_history=None, **kwargs):
        # Hermes has saved the message and the reply by now. Record the reply, then refresh
        # today's transcript off the reply path.
        try:
            questions = _questions_now()
        except Exception:
            questions = None
        writer = _transcript_settings(questions)
        if writer is None:
            try:
                flush_pending()
            except Exception as exc:
                log.warning("nyx-wiki-recall: transcript refresh was not queued (%s)", type(exc).__name__)
            return None
        try:
            transcript.record_reply(
                writer,
                assistant_response=assistant_response,
                conversation_history=conversation_history,
                session_id=str(session_id or ""),
                turn_id=str(kwargs.get("turn_id") or ""),
                platform=str(kwargs.get("platform") or ""),
            )
        except Exception as exc:
            log.warning("nyx-wiki-recall: reply was not recorded (%s)", type(exc).__name__)
        try:
            # The reply was just recorded, so today's file is rebuilt now; a refresh the lookup
            # noted is the same one.
            drop_pending()
            request_refresh(writer)
        except Exception as exc:
            log.warning("nyx-wiki-recall: transcript refresh was not queued (%s)", type(exc).__name__)
        return None

    def _post_hook_off():
        state = shared()
        with state.lock:
            state.post_hook_live = False

    ctx.register_hook("pre_llm_call", pre_llm_call)
    ctx.register_hook("post_llm_call", post_llm_call)

    def _questions_now():
        path = _questions_path(ctx)
        return load(path) if path.is_file() else None

    # Finish yesterday's transcript after midnight, in either position of provider. Not tied to a turn.
    try:
        start_finalizer(_questions_now, Path(__file__).resolve().parent)
    except Exception as exc:
        log.warning("nyx-wiki-recall: day finalization did not start (%s)", type(exc).__name__)
    state = shared()
    with state.lock:
        state.post_hook_live = True
    on_unload = getattr(ctx, "on_unload", None)
    if callable(on_unload):
        try:
            on_unload(_post_hook_off)
        except Exception:
            pass
    ctx.register_tool(
        name="wiki_read",
        toolset="nyx-wiki-recall",
        schema={
            "name": "wiki_read",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "slug": {
                        "type": "string",
                        "description": "Article file name without .md.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Article path relative to the wiki root, ending in .md.",
                    },
                },
                "additionalProperties": False,
            },
        },
        handler=wiki_read,
        description=description,
    )
    # The archivist skill ships with the plugin, as nyx-wiki-recall:wiki-archivist.
    register_skill = getattr(ctx, "register_skill", None)
    if callable(register_skill):
        try:
            register_skill("wiki-archivist", Path(__file__).resolve().parent / "skills" / "wiki-archivist" / "SKILL.md")
        except Exception as exc:
            log.warning("nyx-wiki-recall: archivist skill not registered (%s)", type(exc).__name__)
