import json
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow, ConversationSessionRow, QueryRunRow

router = APIRouter()

_CONTEXT_MAX_LEN = 4000


@router.get("/datasets")
def list_datasets(session: Session = Depends(get_session)):
    rows = session.query(DatasetRow).order_by(DatasetRow.created_at.desc()).all()
    return ok([
        {
            "dataset_id": row.id,
            "filename": row.filename,
            "format": row.format,
            "context": row.context or "",
            "row_count": row.row_count,
            "col_count": row.col_count,
            "columns": json.loads(row.columns_json),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ])


class ContextUpdate(BaseModel):
    context: str


@router.patch("/datasets/{dataset_id}/context")
def update_context(
    dataset_id: str,
    body: ContextUpdate,
    session: Session = Depends(get_session),
):
    if len(body.context) > _CONTEXT_MAX_LEN:
        raise api_error("context_too_long", f"Context must be ≤ {_CONTEXT_MAX_LEN} characters.")

    row = session.get(DatasetRow, dataset_id)
    if row is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)

    row.context = body.context.strip() or None
    return ok({"dataset_id": dataset_id, "context": row.context or ""})


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, session: Session = Depends(get_session)):
    row = session.get(DatasetRow, dataset_id)
    if row is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)

    running = (
        session.query(QueryRunRow)
        .filter(QueryRunRow.dataset_id == dataset_id, QueryRunRow.status == "running")
        .first()
    )
    if running:
        raise api_error("dataset_in_use", "A query is currently running against this dataset.", 409)

    result = _cascade_delete(session, [dataset_id])
    return ok(result)


@router.delete("/datasets")
def delete_all_datasets(session: Session = Depends(get_session)):
    rows = session.query(DatasetRow).all()
    if not rows:
        return ok({"deleted_dataset_ids": [], "deleted_session_count": 0, "deleted_run_count": 0})

    running = session.query(QueryRunRow).filter(QueryRunRow.status == "running").first()
    if running:
        raise api_error("dataset_in_use", "A query is currently running. Cannot delete datasets.", 409)

    result = _cascade_delete(session, [r.id for r in rows])
    return ok(result)


def _cascade_delete(db: Session, dataset_ids: list[str]) -> dict:
    deleted_run_count = 0
    deleted_session_count = 0
    id_set = set(dataset_ids)

    # Find sessions that reference any of these datasets
    all_sessions = db.query(ConversationSessionRow).all()
    sessions_to_delete: set[str] = set()
    for sess in all_sessions:
        sess_ids = set(json.loads(sess.dataset_ids_json)) if sess.dataset_ids_json else {sess.dataset_id}
        if sess_ids & id_set:
            sessions_to_delete.add(sess.id)

    # Delete runs in those sessions + orphan runs directly linked to the datasets
    for sess_id in sessions_to_delete:
        for run in db.query(QueryRunRow).filter(QueryRunRow.session_id == sess_id).all():
            db.delete(run)
            deleted_run_count += 1
        sess_row = db.get(ConversationSessionRow, sess_id)
        if sess_row:
            db.delete(sess_row)
            deleted_session_count += 1

    for did in dataset_ids:
        for run in db.query(QueryRunRow).filter(
            QueryRunRow.dataset_id == did,
            QueryRunRow.session_id.is_(None),
        ).all():
            db.delete(run)
            deleted_run_count += 1

    # Delete dataset rows + CSV files
    for did in dataset_ids:
        row = db.get(DatasetRow, did)
        if row:
            try:
                Path(row.file_path).unlink(missing_ok=True)
            except Exception:
                pass
            db.delete(row)

    return {
        "deleted_dataset_ids": list(dataset_ids),
        "deleted_session_count": deleted_session_count,
        "deleted_run_count": deleted_run_count,
    }
