"""`GET /stats/daily` — today's token + query usage (Phase 2).

Aggregates `completed` `query_runs` created on the server-local day. The active
model comes from settings (default `gemini-3.1-flash-lite`); `context_limit` is
looked up from a small hard-coded table (unknown model -> 128000). Always 200.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from api._common import ok
from config.settings import get_settings
from db.models import QueryRunRow
from db.session import get_session

router = APIRouter()

_DEFAULT_MODEL = "gemini-3.1-flash-lite"

# Approximate input context windows per model (tokens). Unknown -> 128000.
_CONTEXT_LIMITS = {
    # Gemini (the Settings dropdown options)
    "gemini-3.5-flash": 1_000_000,
    "gemini-3.1-pro": 1_000_000,
    "gemini-3.1-flash-lite": 1_000_000,
    "gemini-2.5-pro": 2_000_000,
    "gemini-2.5-flash": 1_000_000,
    "gemini-2.5-flash-lite": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-2.0-flash-lite": 1_000_000,
    "gemini-1.5-flash": 1_000_000,
    "gemini-1.5-pro": 2_000_000,
    # Claude
    "claude-opus-4-8": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}


def _context_limit(model: str) -> int:
    return _CONTEXT_LIMITS.get(model, 128_000)


@router.get("/stats/daily")
def stats_daily(session: Session = Depends(get_session)) -> dict:
    settings = get_settings()
    # Authoritative model: ask the client what the provider will actually call,
    # so the displayed model matches reality (never drifts from a duplicate
    # default). Fall back to the setting/default if client init fails.
    try:
        from llm.client import LLMClient
        model = LLMClient().model or settings.llm_model or _DEFAULT_MODEL
    except Exception:
        model = settings.llm_model or _DEFAULT_MODEL

    now = datetime.now()
    today = now.date()

    completed = session.execute(
        select(QueryRunRow).where(QueryRunRow.status == "completed")
    ).scalars().all()

    # Filter to the server-local day in Python. `created_at` is written as UTC,
    # but SQLite reads it back NAIVE — treat a naive value as UTC, then convert to
    # the local tz before comparing on the local calendar date.
    def _is_today(row: QueryRunRow) -> bool:
        ts = row.created_at
        if ts is None:
            return False
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone().date() == today

    rows = [r for r in completed if _is_today(r)]
    tokens_input = sum(r.tokens_input or 0 for r in rows)
    tokens_output = sum(r.tokens_output or 0 for r in rows)
    query_count = len(rows)

    # The most recent run's input tokens — i.e. the last query's prompt size.
    # This (not the cumulative daily total) is what belongs against the
    # per-request context window. Persisted so the bar shows on reload.
    def _ts(r: QueryRunRow):
        t = r.created_at
        if t is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    latest = max(rows, key=_ts) if rows else None
    last_prompt_tokens = (latest.tokens_input or 0) if latest else 0

    return ok(
        {
            "date": now.date().isoformat(),
            "model": model,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "query_count": query_count,
            "context_limit": _context_limit(model),
            "last_prompt_tokens": last_prompt_tokens,
        }
    )
