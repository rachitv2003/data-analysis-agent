import json
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from data_analyst.api import templates
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow
from data_analyst.graph.nodes import get_provider_name

router = APIRouter()


@router.get("/")
def index(request: Request, session: Session = Depends(get_session)):
    datasets = session.query(DatasetRow).order_by(DatasetRow.created_at.desc()).all()
    dataset_list = [
        {
            "dataset_id": row.id,
            "filename": row.filename,
            "row_count": row.row_count,
            "col_count": row.col_count,
            "columns": json.loads(row.columns_json),
        }
        for row in datasets
    ]
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "datasets": dataset_list,
            "llm_provider": get_provider_name(),
        },
    )
