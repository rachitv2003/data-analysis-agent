from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import ConversationSessionRow, QueryRunRow

router = APIRouter()


@router.get("/sessions/{session_id}")
def get_session_turns(
    session_id: str,
    session: Session = Depends(get_session),
):
    sess = session.get(ConversationSessionRow, session_id)
    if sess is None:
        raise api_error("session_not_found", f"Session {session_id} not found.", 404)

    turns = (
        session.query(QueryRunRow)
        .filter(QueryRunRow.session_id == session_id)
        .order_by(QueryRunRow.created_at)
        .all()
    )

    return ok({
        "session_id": sess.id,
        "dataset_id": sess.dataset_id,
        "turns": [
            {
                "run_id": r.id,
                "question": r.question,
                "answer": r.answer,
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r in turns
        ],
    })
