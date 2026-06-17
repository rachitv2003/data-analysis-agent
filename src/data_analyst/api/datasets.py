import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from data_analyst.api._common import ok
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow

router = APIRouter()


@router.get("/datasets")
def list_datasets(session: Session = Depends(get_session)):
    rows = session.query(DatasetRow).order_by(DatasetRow.created_at.desc()).all()
    return ok([
        {
            "dataset_id": row.id,
            "filename": row.filename,
            "row_count": row.row_count,
            "col_count": row.col_count,
            "columns": json.loads(row.columns_json),
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ])
