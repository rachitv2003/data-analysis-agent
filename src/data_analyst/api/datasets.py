import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow

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
