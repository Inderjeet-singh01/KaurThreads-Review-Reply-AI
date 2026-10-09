"""Per-request timing: where a request's time goes (database, Google, total).

The HTTP middleware in :mod:`app.main` starts a recorder per request; the
credential store and the Google client add their durations to it. The result
is logged (one line per request) and returned as a ``Server-Timing`` header,
shown in the browser's DevTools (Network -> request -> Timing).

Only durations, call counts, the method and the path are recorded — never
query strings, bodies, tokens or review content.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

# component -> [total milliseconds, number of calls]
Recorder = dict[str, list[float]]

_current: ContextVar[Recorder | None] = ContextVar("request_timing", default=None)


def begin() -> tuple[Recorder, Token]:
    recorder: Recorder = {}
    return recorder, _current.set(recorder)


def end(token: Token) -> None:
    _current.reset(token)


@contextmanager
def measure(component: str) -> Iterator[None]:
    """Add the duration of the block to ``component`` (no-op outside a request)."""
    recorder = _current.get()
    start = time.perf_counter()
    try:
        yield
    finally:
        if recorder is not None:
            entry = recorder.setdefault(component, [0.0, 0])
            entry[0] += (time.perf_counter() - start) * 1000
            entry[1] += 1


def summary(recorder: Recorder) -> str:
    """``db=12ms/1 google=640ms/3`` for the log line."""
    return " ".join(
        f"{name}={total:.0f}ms/{int(count)}" for name, (total, count) in sorted(recorder.items())
    ) or "no db/google calls"


def server_timing(recorder: Recorder, total_ms: float) -> str:
    parts = [
        f'{name};dur={total:.1f};desc="{int(count)} call(s)"'
        for name, (total, count) in sorted(recorder.items())
    ]
    parts.append(f"total;dur={total_ms:.1f}")
    return ", ".join(parts)
