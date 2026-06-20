import json as _json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import ConversationSessionRow, QueryRunRow
from data_analyst.utils.markdown import render_markdown

router = APIRouter()


class SessionNameUpdate(BaseModel):
    name: str


@router.get("/sessions")
def list_all_sessions(session: Session = Depends(get_session)):
    """Return all sessions across all datasets, most-recently-updated first."""
    all_sessions = (
        session.query(ConversationSessionRow)
        .order_by(ConversationSessionRow.updated_at.desc())
        .all()
    )
    result = []
    for s in all_sessions:
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
            "name": s.name,
            "created_at": s.created_at.isoformat(),
            "updated_at": s.updated_at.isoformat(),
            "turn_count": turn_count or 0,
            "first_question": first_run.question if first_run else "",
        })
    return ok(result)


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

    # C28: resolve dataset_ids for Database tab session scoping
    if sess.dataset_ids_json:
        dataset_ids = _json.loads(sess.dataset_ids_json)
    else:
        dataset_ids = [sess.dataset_id]

    return ok({
        "session_id": sess.id,
        "dataset_id": sess.dataset_id,
        "dataset_ids": dataset_ids,
        "name": sess.name,
        "turns": [
            {
                "run_id": r.id,
                "question": r.question,
                "answer_markdown": r.answer or "",
                "answer_html": render_markdown(r.answer or ""),
                "iteration_count": r.iteration_count,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "status": r.status,
                "is_best_effort": r.error_message in ("max_iterations", "consecutive_errors"),
                "steps": _json.loads(r.action_history) if r.action_history else [],
                "created_at": r.created_at.isoformat(),
                "prompt_breakdown": _json.loads(r.prompt_breakdown) if r.prompt_breakdown else None,
            }
            for r in turns
        ],
    })


@router.patch("/sessions/{session_id}/name")
def rename_session(
    session_id: str,
    body: SessionNameUpdate,
    session: Session = Depends(get_session),
):
    sess = session.get(ConversationSessionRow, session_id)
    if sess is None:
        raise api_error("session_not_found", f"Session {session_id} not found.", 404)
    sess.name = body.name.strip() or None
    return ok({"session_id": sess.id, "name": sess.name})


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: str,
    session: Session = Depends(get_session),
):
    sess = session.get(ConversationSessionRow, session_id)
    if sess is None:
        raise api_error("session_not_found", f"Session {session_id} not found.", 404)
    session.query(QueryRunRow).filter(QueryRunRow.session_id == session_id).delete()
    session.delete(sess)
    # C27: evict session DataFrame cache
    try:
        from data_analyst.graph.nodes import _evict_session
        _evict_session(session_id)
    except Exception:
        pass
    return ok({"deleted": session_id})


@router.delete("/sessions")
def delete_all_sessions(session: Session = Depends(get_session)):
    session_ids = [s.id for s in session.query(ConversationSessionRow.id).all()]
    session.query(QueryRunRow).filter(QueryRunRow.session_id.isnot(None)).delete()
    session.query(ConversationSessionRow).delete()
    # C27: evict all session DataFrame caches
    try:
        from data_analyst.graph.nodes import _evict_session
        for sid in session_ids:
            _evict_session(sid)
    except Exception:
        pass
    return ok({"deleted": "all"})
