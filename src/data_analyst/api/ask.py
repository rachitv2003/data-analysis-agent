from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow, QueryRunRow
from data_analyst.graph.runner import run_agent
from data_analyst.utils.markdown import render_markdown

router = APIRouter()


class AskRequest(BaseModel):
    dataset_id: str
    question: str
    session_id: str | None = None


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

    try:
        run_id, session_id = run_agent(body.dataset_id, body.question, body.session_id)
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise api_error("session_not_found", msg, 404)
        raise api_error("session_error", msg, 400)

    run = session.get(QueryRunRow, run_id)
    if run is None:
        raise api_error("run_not_found", "Agent run record not found.", 500)

    answer_md = run.answer or ""
    return ok({
        "run_id": run.id,
        "session_id": session_id,
        "answer_markdown": answer_md,
        "answer_html": render_markdown(answer_md),
        "iteration_count": run.iteration_count,
        "tokens_input": run.tokens_input,
        "tokens_output": run.tokens_output,
        "status": run.status,
    })
