from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from data_analyst.api._common import ok
from data_analyst.config.settings import get_settings
from data_analyst.db.session import get_session
from data_analyst.db.models import QueryRunRow

router = APIRouter()

# C29: hard-coded context window limits per model (tokens)
_CONTEXT_LIMITS: dict[str, int] = {
    "gemini-3.1-flash-lite": 1_000_000,
    "gemini-3.1-flash":      1_000_000,
    "gemini-3.1-pro":        1_000_000,
    "gemini-2.5-flash-lite": 1_000_000,
    "gemini-2.5-flash":      1_000_000,
    "gemini-2.5-pro":        1_000_000,
    "gemini-2.0-flash":      1_000_000,
    "gemini-1.5-pro":        2_097_152,
    "gemini-1.5-flash":      1_048_576,
    "claude-opus-4":           200_000,
    "claude-sonnet-4":         200_000,
    "claude-haiku-4":          200_000,
    "claude-3-5-sonnet":       200_000,
    "claude-3-5-haiku":        200_000,
    "claude-3-opus":           200_000,
    "gpt-4o":                  128_000,
    "gpt-4o-mini":             128_000,
    "gpt-4-turbo":             128_000,
}


def get_context_limit(model: str) -> int:
    """Return context window size for a model, falling back to 128 000."""
    model_lower = model.lower()
    for key, limit in _CONTEXT_LIMITS.items():
        if key in model_lower:
            return limit
    # Unlisted Gemini models all have large context windows
    if "gemini" in model_lower:
        return 1_000_000
    return 128_000


@router.get("/stats/daily")
def daily_stats(session: Session = Depends(get_session)):
    # Compare using server local time — SQLite datetime('localtime') converts stored
    # UTC timestamps to the server's local timezone (IST when running in India).
    today = datetime.now().date().isoformat()
    row = (
        session.query(
            func.coalesce(func.sum(QueryRunRow.tokens_input), 0).label("tokens_input"),
            func.coalesce(func.sum(QueryRunRow.tokens_output), 0).label("tokens_output"),
            func.count(QueryRunRow.id).label("query_count"),
        )
        .filter(
            QueryRunRow.status == "completed",
            func.date(func.datetime(QueryRunRow.created_at, "localtime")) == today,
        )
        .one()
    )
    model = get_settings().llm_model
    return ok({
        "date": today,
        "model": model,
        "tokens_input": int(row.tokens_input),
        "tokens_output": int(row.tokens_output),
        "query_count": int(row.query_count),
        "context_limit": get_context_limit(model),
    })
