"""Where the plugin keeps the files it writes.

Hermes documents one home for plugin data: ``plugins.plugin_storage.plugin_data_dir(name)``,
which is ``<hermes home>/plugin-data/<name>/``. It survives ``hermes plugins update`` and
``remove`` and follows the active profile. Outside Hermes (tests, a script) the same folder is
built from ``hermes_constants.get_hermes_home()``, then the ``HERMES_HOME`` variable, then
``~/.hermes``.
"""

from __future__ import annotations

import os
from pathlib import Path

PLUGIN_NAME = "nyx-wiki-recall"


def hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        pass
    raw = os.environ.get("HERMES_HOME", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".hermes"


def data_dir() -> Path:
    """<hermes home>/plugin-data/nyx-wiki-recall/, created on first use."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        return Path(plugin_data_dir(PLUGIN_NAME))
    except Exception:
        pass
    root = hermes_home() / "plugin-data" / PLUGIN_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


_OFF = {"off", "no", "none", "false", "0"}


def configured_path(questions, key: str, default_name: str) -> Path | None:
    """questions[key] as a path. Blank means data_dir()/default_name. off means no file."""
    raw = questions.get(key, "").strip()
    if raw.lower() in _OFF:
        return None
    if raw:
        return Path(raw).expanduser()
    return data_dir() / default_name
