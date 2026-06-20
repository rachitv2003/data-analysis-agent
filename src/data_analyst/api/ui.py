from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from data_analyst.api import templates
from data_analyst.api.datasets import _row_dict
from data_analyst.config.settings import get_settings
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow
from data_analyst.graph.nodes import get_provider_name

router = APIRouter()


@router.get("/")
def index(request: Request, session: Session = Depends(get_session)):
    datasets = session.query(DatasetRow).order_by(DatasetRow.created_at.desc()).all()
    dataset_list = [_row_dict(row, session) for row in datasets]
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "datasets": dataset_list,
            "llm_provider": get_provider_name(),
            "llm_model": get_settings().llm_model,
        },
    )
