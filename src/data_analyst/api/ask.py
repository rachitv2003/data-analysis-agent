from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow, QueryRunRow
from data_analyst.graph.runner import run_agent

router = APIRouter()


class AskRequest(BaseModel):
    dataset_id: str
    question: str


@router.post("/ask")
def ask_question(
    body: AskRequest,
    session: Session = Depends(get_session),
):
    if not body.question.strip():
        raise api_error("empty_question", "Question cannot be empty.")

    dataset = session.get(DatasetRow, body.dataset_id)
    if dataset is None:
        raise api_error("dataset_not_found", f"Dataset {body.dataset_id} not found.", 404)

    run_id = run_agent(body.dataset_id, body.question)

    run = session.get(QueryRunRow, run_id)
    if run is None:
        raise api_error("run_not_found", "Agent run record not found.", 500)

    return ok({
        "run_id": run.id,
        "answer": run.answer,
        "iteration_count": run.iteration_count,
        "status": run.status,
    })
