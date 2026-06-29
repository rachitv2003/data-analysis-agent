"""Cooperative cancellation for in-flight agent runs.

A **Stop** from the UI aborts the `/ask` fetch AND calls `POST /runs/{id}/cancel`,
which flags the run here. The ReAct graph checks `is_cancelled(run_id)` at its
routing edges and at `plan_action` and wraps up immediately — `force_finalize`
short-circuits its synthesis LLM call and returns a "stopped" answer, so no more
model calls or iterations run. The runner `discard`s the flag when the run ends.

Thread-safe: under FastAPI the cancel request is served on a different threadpool
thread than the one executing the run, so the set is guarded by a lock.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_cancelled: set[str] = set()


def request_cancel(run_id: str) -> None:
    """Flag `run_id` for cooperative cancellation (no-op for a falsy id)."""
    if not run_id:
        return
    with _lock:
        _cancelled.add(run_id)


def is_cancelled(run_id: str) -> bool:
    """Whether `run_id` has been asked to stop."""
    if not run_id:
        return False
    with _lock:
        return run_id in _cancelled


def discard(run_id: str) -> None:
    """Clear a run's cancel flag (called when the run ends, success or not)."""
    if not run_id:
        return
    with _lock:
        _cancelled.discard(run_id)
