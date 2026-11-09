"""Hand one lookup from the hook to the memory provider in the same turn.

Hermes runs the hook first, then asks the memory provider. The hook has up to
30 seconds. The provider is expected back in a few seconds. The hook does the
lookup and leaves the text here. The provider reads it and returns at once.

Each turn is looked up, and written to the transcript notes, once. Hermes can
tidy the message before it reaches the provider (skill text removed, secrets
masked), so a note left by the hook for the same session a moment ago still
belongs to this turn even when the words differ.

Hermes imports this plugin twice, under two module names: once for the hook
and once as the memory provider. The state lives in one object both copies
find in sys.modules, so the hook and the provider see the same hand-off.
"""

from __future__ import annotations

import sys
import threading
import time
import types

_SAME_TURN_SECONDS = 60.0
_SHARED_NAME = "_nyx_wiki_recall_shared_state"


def _new_state() -> types.ModuleType:
    state = types.ModuleType(_SHARED_NAME)
    state.lock = threading.RLock()
    state.ready = False
    state.owners = set()
    state.slot = None
    state.post_hook_live = False
    state.pending_script = None
    state.refresh_wanted = None
    state.refresh_due = 0.0
    state.refresh_worker = None
    return state


def shared() -> types.ModuleType:
    """The one state object for every copy of this plugin in the process."""
    state = sys.modules.get(_SHARED_NAME)
    if state is None:
        state = sys.modules.setdefault(_SHARED_NAME, _new_state())
    return state


def mark_ready(value: bool) -> None:
    state = shared()
    with state.lock:
        state.ready = bool(value)


def provider_up(owner: object) -> None:
    """One memory provider instance is serving. Several agents can share this process."""
    state = shared()
    with state.lock:
        state.owners.add(owner)


def provider_down(owner: object) -> None:
    """That instance stopped. The others keep the hand-off on."""
    state = shared()
    with state.lock:
        state.owners.discard(owner)


def ready() -> bool:
    state = shared()
    with state.lock:
        return state.ready or bool(state.owners)


def store(session_id: str, query: str, text: str, count: int | None) -> None:
    state = shared()
    with state.lock:
        state.slot = {
            "session_id": session_id or "",
            "query": (query or "").strip(),
            "text": text or "",
            "count": count,
            "at": time.monotonic(),
        }


def take(session_id: str, query: str) -> tuple[str, int | None] | None:
    state = shared()
    with state.lock:
        slot = state.slot
        if slot is None:
            return None
        if session_id and slot["session_id"] and slot["session_id"] != session_id:
            return None
        if slot["query"] != (query or "").strip():
            same_turn = (
                bool(session_id)
                and slot["session_id"] == session_id
                and time.monotonic() - slot.get("at", 0.0) <= _SAME_TURN_SECONDS
            )
            if not same_turn:
                return None
        state.slot = None
        return slot["text"], slot["count"]