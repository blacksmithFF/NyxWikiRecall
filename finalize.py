"""Finish each day's transcript when it ends, inside the plugin, so no separate nightly exporter is needed.

A transcript day runs from finalize.after (02:00 by default) to finalize.after on the next date, so a
turn at 01:30 belongs to the day before. A background thread wakes every finalize.check.minutes. Once the
local clock passes finalize.after on the next date, it writes the finished day one last time, wiki pages included, and notes the day in
finalize.state. The built-in writer (transcript.py) does the writing. A day already in that file is
never written again. If Hermes was down at
that time, the next check writes every missed day in order (at most finalize.catchup.days), never a day
before finalize.from, and never a day that came before the plugin first ran.

After each check, keep.days (0, the default, keeps everything) removes the plugin's own working
files for finished days older than that many days: journal/YYYY-MM-DD.jsonl and
suggestions/YYYY-MM-DD.jsonl. The rendered transcripts, transcriptIndex.md, finalize.state and the
wiki are never removed.

The thread runs in either position of provider; it does not wait for a turn.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path

if __package__:
    from . import transcript as builtin
    from .bridge import shared
    from .storage import data_dir
else:
    import transcript as builtin
    from bridge import shared
    from storage import data_dir

log = logging.getLogger("nyx-wiki-recall")

DEFAULT_AFTER = time(2, 0)
DEFAULT_CHECK_MINUTES = 10.0
DEFAULT_CATCHUP_DAYS = 7
STATE_NAME = "finalized.json"
CLEANUP_LOCK = ".nyx-wiki-recall-cleanup.lock"
# Results that mean the day's transcript is complete, so its working files are no longer needed.
# foreign (someone else's file was in the way) keeps them: the journal is then the only record.
_DONE = ("final", "empty")
_DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.jsonl$")


def settings(questions, plugin_dir: Path | None = None) -> dict | None:
    """None when finishing days is off, or transcripts are off.

    plugin_dir is accepted for older callers and not used: state lives in the plugin's data folder.
    """
    del plugin_dir
    if not questions.flag("finalize", True):
        return None
    writer = builtin.settings(questions)
    if writer is None:
        return None
    after = builtin.day_ends(questions)
    try:
        check = max(1.0, float(questions.get("finalize.check.minutes", "") or DEFAULT_CHECK_MINUTES))
    except ValueError:
        check = DEFAULT_CHECK_MINUTES
    try:
        catchup = max(1, int(questions.get("finalize.catchup.days", "") or DEFAULT_CATCHUP_DAYS))
    except ValueError:
        catchup = DEFAULT_CATCHUP_DAYS
    floor = None
    raw_from = questions.get("finalize.from", "").strip()
    if raw_from:
        try:
            floor = date.fromisoformat(raw_from)
        except ValueError:
            log.warning("nyx-wiki-recall: finalize.from %r is not YYYY-MM-DD; ignored", raw_from)
    state = questions.get("finalize.state", "").strip()
    return {
        "writer": writer,
        "after": after,
        "check_seconds": check * 60.0,
        "catchup": catchup,
        "floor": floor,
        "state": Path(state).expanduser() if state else data_dir() / STATE_NAME,
        "keep_days": keep_days(questions),
    }


def keep_days(questions) -> int:
    """keep.days: delete the plugin's working files for finished days older than this. 0 keeps everything."""
    raw = questions.get("keep.days", "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        log.warning("nyx-wiki-recall: keep.days %r is not a whole number; keeping everything", raw)
        return 0


def last_due_day(now: datetime, after: time) -> date:
    """The newest finished day. A day ends at after on the next date (transcript.day_at), so at or after
    02:00 on day D the day D-1 is due; before 02:00 it is still running and D-2 is the newest finished."""
    current = (now - timedelta(hours=after.hour, minutes=after.minute)).date()
    return current - timedelta(days=1)


def load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # One temp name per process and thread: a CLI and a gateway on one Hermes home can both save.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def through_of(data: dict) -> date | None:
    try:
        return date.fromisoformat(str(data.get("final_through") or ""))
    except ValueError:
        return None


def due_days(now: datetime, cfg: dict, data: dict) -> list[date]:
    last = last_due_day(now, cfg["after"])
    through = through_of(data)
    start = (through + timedelta(days=1)) if through is not None else last + timedelta(days=1)
    if cfg["floor"] is not None:
        start = max(start, cfg["floor"])
    start = max(start, last - timedelta(days=cfg["catchup"] - 1))
    days = []
    cursor = start
    while cursor <= last:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def finalize_builtin(writer: dict, day: date, now: datetime) -> str:
    """Write day as final with the built-in writer. final, empty, foreign, or busy (retry later).

    A day already final stays as it is.
    """
    result = builtin.write_day(writer, day, status="final", now=now)
    if result in ("wrote", "unchanged", "final"):
        return "final"
    return result


def tick(questions, plugin_dir: Path, now: datetime | None = None) -> list[tuple[str, str]]:
    """One check. Writes every due day not yet finished, then applies keep.days. Returns (day, result) pairs."""
    cfg = settings(questions, plugin_dir)
    if cfg is None:
        return []
    now = now or datetime.now()
    data = load_state(cfg["state"])
    if through_of(data) is None:
        # First run: only days that finish from now on are this plugin's to write.
        seed = last_due_day(now, cfg["after"])
        if cfg["floor"] is not None:
            seed = max(seed, cfg["floor"] - timedelta(days=1))
        data = {"final_through": seed.isoformat(), "days": {}, "note": "seeded on first run"}
        save_state(cfg["state"], data)
    days = due_days(now, cfg, data)
    if not days and cfg["keep_days"] <= 0:
        return []
    with shared().lock:
        lock = getattr(shared(), "finalize_lock", None)
        if lock is None:
            lock = shared().finalize_lock = threading.Lock()
    if not lock.acquire(blocking=False):
        return []
    done: list[tuple[str, str]] = []
    try:
        for day in days:
            result = finalize_builtin(cfg["writer"], day, now)
            done.append((day.isoformat(), result))
            if result == "busy":
                log.info("nyx-wiki-recall: %s not final yet (%s); next check retries", day, result)
                break
            data = load_state(cfg["state"]) or data
            data.setdefault("days", {})[day.isoformat()] = {
                "result": result, "at": datetime.now().isoformat(timespec="seconds"),
            }
            data["final_through"] = day.isoformat()
            save_state(cfg["state"], data)
            log.info("nyx-wiki-recall: transcript for %s finalized (%s)", day, result)
        # After the day is written: the working files of old finished days can go.
        if cfg["keep_days"] > 0:
            try:
                clean_up(cfg, load_state(cfg["state"]) or data, now)
            except Exception as exc:
                log.warning("nyx-wiki-recall: old working files were not cleaned up (%s)", type(exc).__name__)
    finally:
        lock.release()
    return done


def removable_days(cfg: dict, data: dict, now: datetime) -> set[date]:
    """Days whose working files keep.days may delete. Empty when keep.days is 0.

    A day qualifies only when all of these hold:
    - it is older than keep.days (today minus the day is more than keep.days days);
    - it is before final_through, so the next day, which reads this day's files as its neighbour,
      is finished too;
    - finalize.state records it as final or empty (never foreign, never a day it skipped);
    - for final, its transcript file is there and says Status: final.
    """
    keep = int(cfg.get("keep_days") or 0)
    through = through_of(data)
    if keep <= 0 or through is None:
        return set()
    cutoff = now.date() - timedelta(days=keep)
    writer = cfg["writer"]
    out: set[date] = set()
    for key, info in (data.get("days") or {}).items():
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        result = info.get("result") if isinstance(info, dict) else None
        if day >= cutoff or day >= through or result not in _DONE:
            continue
        if result == "final" and builtin.file_status(Path(writer["folder"]) / builtin.daily_name(day)) != "final":
            continue
        out.add(day)
    return out


def working_folders(cfg: dict) -> list[Path]:
    """The folders keep.days cleans: the journal and the per-day suggestions. Never the transcripts."""
    writer = cfg["writer"]
    folders = [Path(writer["journal"])]
    if writer.get("suggestions"):
        folders.append(Path(writer["suggestions"]))
    transcripts = Path(writer["folder"]).resolve()
    return [folder for folder in folders if folder.resolve() != transcripts]


def clean_up(cfg: dict, data: dict, now: datetime) -> list[Path]:
    """Delete working files (YYYY-MM-DD.jsonl only) for removable days. Returns what was removed.

    One process at a time across Hermes processes on one home (a lock file next to finalize.state);
    a file another process already removed is skipped.
    """
    days = removable_days(cfg, data, now)
    if not days:
        return []
    lock = Path(cfg["state"]).parent / CLEANUP_LOCK
    if not builtin._acquire(lock):
        return []
    removed: list[Path] = []
    try:
        for folder in working_folders(cfg):
            if not folder.is_dir():
                continue
            for path in sorted(folder.iterdir()):
                match = _DAY_FILE.match(path.name)
                if not match or not path.is_file():
                    continue
                try:
                    day = date.fromisoformat(match.group(1))
                except ValueError:
                    continue
                if day not in days:
                    continue
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    log.warning("nyx-wiki-recall: could not remove %s (%s)", path.name, type(exc).__name__)
                    continue
                removed.append(path)
    finally:
        builtin._release(lock)
    if removed:
        log.info("nyx-wiki-recall: keep.days = %d removed %d old working file(s): %s", cfg["keep_days"],
                 len(removed), ", ".join(f"{p.parent.name}/{p.name}" for p in removed))
    return removed


def next_run(questions, plugin_dir: Path, now: datetime | None = None) -> datetime | None:
    """When the next day will be finished. None when finishing is off."""
    cfg = settings(questions, plugin_dir)
    if cfg is None:
        return None
    now = now or datetime.now()
    data = load_state(cfg["state"])
    if due_days(now, cfg, data):
        return now
    nxt = datetime.combine(now.date(), cfg["after"])
    return nxt if nxt > now else nxt + timedelta(days=1)


def start(runtime, plugin_dir: Path) -> None:
    """One thread per process. A reload swaps the code the thread calls, not the thread."""
    state = shared()

    def run_once() -> float:
        questions = runtime()
        if questions is None:
            return DEFAULT_CHECK_MINUTES * 60.0
        cfg = settings(questions, plugin_dir)
        try:
            tick(questions, plugin_dir)
        except Exception as exc:
            log.warning("nyx-wiki-recall: day finalization failed (%s: %s)", type(exc).__name__, exc)
        return cfg["check_seconds"] if cfg else DEFAULT_CHECK_MINUTES * 60.0

    try:
        questions = runtime()
        nxt = next_run(questions, plugin_dir) if questions is not None else None
        cfg = settings(questions, plugin_dir) if questions is not None else None
        if nxt is not None and cfg is not None:
            through = through_of(load_state(cfg["state"]))
            log.info("nyx-wiki-recall: day finalization on; finished through %s; next day is written at %s",
                     through or "(not yet)", nxt.strftime("%Y-%m-%d %H:%M"))
        else:
            log.info("nyx-wiki-recall: day finalization off")
    except Exception as exc:
        log.warning("nyx-wiki-recall: day finalization schedule unknown (%s)", type(exc).__name__)
    with state.lock:
        state.finalize_once = run_once
        worker = getattr(state, "finalize_worker", None)
        if worker is not None and worker.is_alive():
            return
        worker = threading.Thread(target=_loop, name="nyx-wiki-recall-finalize", daemon=True)
        state.finalize_worker = worker
    worker.start()


def _loop() -> None:
    state = shared()
    _time.sleep(30.0)  # let Hermes finish starting
    while True:
        once = getattr(state, "finalize_once", None)
        wait = DEFAULT_CHECK_MINUTES * 60.0
        if once is not None:
            try:
                wait = once()
            except Exception as exc:
                log.warning("nyx-wiki-recall: day finalization check failed (%s)", type(exc).__name__)
        _time.sleep(max(30.0, wait))