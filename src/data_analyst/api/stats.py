from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from data_analyst.api._common import ok
from data_analyst.config.settings import get_settings
from data_analyst.db.session import get_session
from data_analyst.db.models import QueryRunRow

router = APIRouter()


@router.get("/stats/daily")
def daily_stats(session: Session = Depends(get_session)):
    today = date.today().isoformat()
    row = (
        session.query(
            func.coalesce(func.sum(QueryRunRow.tokens_input), 0).label("tokens_input"),
            func.coalesce(func.sum(QueryRunRow.tokens_output), 0).label("tokens_output"),
            func.count(QueryRunRow.id).label("query_count"),
        )
        .filter(
            QueryRunRow.status == "completed",
            func.date(QueryRunRow.created_at) == today,
        )
        .one()
    )
    return ok({
        "date": today,
        "model": get_settings().llm_model,
        "tokens_input": int(row.tokens_input),
        "tokens_output": int(row.tokens_output),
        "query_count": int(row.query_count),
    })
