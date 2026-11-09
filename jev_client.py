"""Ask JEV through OpenRouter's Decisions API. One request, many yes/no questions."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

log = logging.getLogger("nyx-wiki-recall")

Opener = Callable[..., Any]


def api_key() -> str:
    """OPENROUTER_API_KEY through Hermes's secret API. "" when it is not set or Hermes is not there.

    Quiet on purpose: it is read once per message, and the plugin says once at load when it is missing.
    """
    try:
        from agent.secret_scope import get_secret
    except Exception:
        return ""
    try:
        return get_secret("OPENROUTER_API_KEY") or ""
    except Exception:
        return ""


NO_KEY_WARNING = (
    "nyx-wiki-recall: OPENROUTER_API_KEY is not set, so wiki lookups are off: no glimpses are added and "
    "no Wiki pages: lines are written. Transcripts are still written. Set the key the usual Hermes way "
    "to turn lookups on; the next message uses it."
)


class Scores(dict):
    """Scores by question id. error says why JEV gave none (timeout, HTTP 503, ...). Empty when it answered."""

    def __init__(self, *args, error: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.error = error


def build_body(model: str, message: str, questions: list[dict[str, str]]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "state": {"message": message},
        "questions": {},
    }
    for item in questions:
        payload["questions"][item["id"]] = {
            "type": "noul",
            "instructions": item["instructions"],
            "criteria": {"true": item["yes"], "false": item["no"]},
        }
    return payload


def score_gap(payload: Any) -> str:
    """Why a response produced no usable score. Empty when at least one score parsed."""
    if not isinstance(payload, dict):
        return "unexpected answer shape"
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        return "unexpected answer shape"
    saw_noul = False
    for answer in answers.values():
        if not isinstance(answer, dict) or "noul" not in answer:
            continue
        saw_noul = True
        try:
            float(answer["noul"])
        except (TypeError, ValueError):
            continue
        return ""
    if not answers or not saw_noul:
        return "no noul scores"
    return "no scores"


def parse_answers(payload: Any) -> dict[str, float]:
    answers = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(answers, dict):
        return {}
    scores: dict[str, float] = {}
    for key, answer in answers.items():
        if not isinstance(answer, dict) or "noul" not in answer:
            continue
        try:
            scores[str(key)] = float(answer["noul"])
        except (TypeError, ValueError):
            continue
    return scores


def _redact(text: str, key: str) -> str:
    if key and key in text:
        return text.replace(key, "[redacted]")
    return text


def post_json(
    url: str,
    body: dict[str, Any],
    key: str,
    timeout: float,
    opener: Opener = urllib.request.urlopen,
) -> dict[str, float]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        log.warning("nyx-wiki-recall: JEV HTTP %s", exc.code)
        return Scores(error=f"HTTP {exc.code}")
    except json.JSONDecodeError:
        log.warning("nyx-wiki-recall: JEV answer was not JSON")
        return Scores(error="answer was not JSON")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        text = _redact(str(exc), key)
        log.warning("nyx-wiki-recall: JEV request failed (%s)", text[:200])
        timed_out = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError)
        return Scores(error="timeout" if timed_out or "timed out" in text.lower() else "request failed")
    scores = parse_answers(payload)
    gap = score_gap(payload)
    if gap:
        log.info("nyx-wiki-recall: JEV %s", gap)
    return Scores(scores, error=gap if not scores else "")


def ask(
    message: str,
    questions: list[dict[str, str]],
    timeout: float,
    *,
    model: str,
    url: str,
    key: str,
    opener: Opener = urllib.request.urlopen,
) -> dict[str, float]:
    if not key:
        log.info("nyx-wiki-recall: JEV skipped (no key)")
        return Scores(error="no key")
    if not questions:
        return Scores()
    if timeout <= 0:
        return Scores(error="timeout")
    body = build_body(model, message, questions)
    return post_json(url, body, key, timeout, opener)
