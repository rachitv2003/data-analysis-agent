from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow, ConversationSessionRow, QueryRunRow

router = APIRouter()


@router.get("/datasets/{dataset_id}/sessions")
def list_dataset_sessions(
    dataset_id: str,
    session: Session = Depends(get_session),
):
    dataset = session.get(DatasetRow, dataset_id)
    if dataset is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)

    sessions = (
        session.query(ConversationSessionRow)
        .filter(ConversationSessionRow.dataset_id == dataset_id)
        .order_by(ConversationSessionRow.updated_at.desc())
        .all()
    )

    result = []
    for s in sessions:
        first_run = (
            session.query(QueryRunRow)
            .filter(QueryRunRow.session_id == s.id)
            .order_by(QueryRunRow.created_at)
            .first()
        )
        turn_count = (
            session.query(func.count(QueryRunRow.id))
            .filter(QueryRunRow.session_id == s.id)
            .scalar()
        )
        result.append({
            "session_id": s.id,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
            "turn_count": turn_count or 0,
            "first_question": first_run.question if first_run else "",
        })

    return ok(result)
