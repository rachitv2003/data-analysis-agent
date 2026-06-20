import json as _json

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow, QueryRunRow, ConversationSessionRow
from data_analyst.graph.nodes import generate_suggestions
from data_analyst.graph.runner import run_agent
from data_analyst.graph.selector import select_datasets
from data_analyst.utils.markdown import render_markdown

router = APIRouter()


class AskRequest(BaseModel):
    dataset_id: str | None = None        # backward compat — single dataset
    dataset_ids: list[str] | None = None # explicit multi-dataset; if omitted C19 auto-selects
    question: str
    session_id: str | None = None
    skip_clarification: bool = False     # set by frontend after first clarification response


@router.post("/ask")
def ask_question(
    body: AskRequest,
    session: Session = Depends(get_session),
):
    if not body.question.strip():
        raise api_error("empty_question", "Question cannot be empty.")

    explicit_ids: list[str] | None = None
    if body.dataset_ids is not None:
        explicit_ids = body.dataset_ids
    elif body.dataset_id is not None:
        explicit_ids = [body.dataset_id]

    if explicit_ids is not None:
        # Opt-out path: caller supplied IDs — validate and skip selector + clarification
        for did in explicit_ids:
            if session.get(DatasetRow, did) is None:
                raise api_error("dataset_not_found", f"Dataset {did} not found.", 404)
        full_ids = explicit_ids
        sandbox_ids = explicit_ids
        selector_reasoning: str | None = None
        sel_ti, sel_to = 0, 0
    else:
        # C19 auto-selection path: fetch all datasets, run selector
        all_datasets = session.query(DatasetRow).order_by(DatasetRow.created_at).all()
        if not all_datasets:
            raise api_error("no_datasets", "No datasets uploaded yet.", 400)
        full_ids = [ds.id for ds in all_datasets]

        # C26: pre-flight clarification check (fail-open, 60s timeout)
        history: list[dict] = []
        if body.session_id:
            prior_runs = (
                session.query(QueryRunRow)
                .filter(
                    QueryRunRow.session_id == body.session_id,
                    QueryRunRow.status == "completed",
                )
                .order_by(QueryRunRow.created_at)
                .all()
            )
            history = [
                {"question": r.question, "answer": r.answer or ""}
                for r in prior_runs[-5:]
            ]

        datasets_for_clarify = [
            {
                "filename": ds.filename,
                "row_count": ds.row_count,
                "col_count": ds.col_count,
                "columns": _json.loads(ds.columns_json),
            }
            for ds in all_datasets
        ]

        from data_analyst.graph.clarify import check_clarification
        clarify = (
            check_clarification(body.question, datasets_for_clarify, history)
            if not body.skip_clarification
            else type("_", (), {"needs_clarification": False, "question": "", "tokens_input": 0, "tokens_output": 0})()
        )

        if clarify.needs_clarification:
            # Resolve or create session for this clarification turn
            if body.session_id:
                sess_row = session.get(ConversationSessionRow, body.session_id)
            else:
                sess_row = None

            if sess_row is None:
                primary_id = all_datasets[0].id
                ds_ids_json = _json.dumps(full_ids) if len(full_ids) > 1 else None
                sess_row = ConversationSessionRow(
                    dataset_id=primary_id,
                    dataset_ids_json=ds_ids_json,
                )
                session.add(sess_row)
                session.flush()

            clarify_run = QueryRunRow(
                dataset_id=sess_row.dataset_id,
                session_id=sess_row.id,
                question=body.question,
                answer=clarify.question,
                status="clarification",
                iteration_count=0,
                tokens_input=clarify.tokens_input or 0,
                tokens_output=clarify.tokens_output or 0,
            )
            session.add(clarify_run)
            session.flush()
            session.commit()

            return ok({
                "type": "clarification",
                "run_id": clarify_run.id,
                "session_id": sess_row.id,
                "clarification_question": clarify.question,
            })

        sandbox_ids, selector_reasoning, sel_ti, sel_to = select_datasets(body.question, all_datasets)

    try:
        run_id, session_id = run_agent(
            full_ids,
            body.question,
            body.session_id,
            sandbox_dataset_ids=sandbox_ids,
            selector_reasoning=selector_reasoning,
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg:
            raise api_error("session_not_found", msg, 404)
        raise api_error("session_error", msg, 400)

    run = session.get(QueryRunRow, run_id)
    if run is None:
        raise api_error("run_not_found", "Agent run record not found.", 500)

    # Add selector LLM tokens (counted before the agent graph runs)
    if sel_ti or sel_to:
        run.tokens_input = (run.tokens_input or 0) + sel_ti
        run.tokens_output = (run.tokens_output or 0) + sel_to
        session.add(run)
        session.commit()

    answer_md = run.answer or ""

    # Resolve filenames for the datasets actually loaded into the sandbox
    datasets_used = []
    for did in sandbox_ids:
        ds = session.get(DatasetRow, did)
        if ds:
            datasets_used.append({"id": did, "filename": ds.filename})

    is_best_effort = run.error_message in ("max_iterations", "consecutive_errors")
    steps = _json.loads(run.action_history) if run.action_history else []
    derived_dataset_ids = [
        r.id for r in session.query(DatasetRow).filter(
            DatasetRow.derived_from_run_id == run_id
        ).all()
    ]
    suggested_questions, sug_ti, sug_to = generate_suggestions(body.question, answer_md)

    # Add suggestion-call tokens to the run totals
    if sug_ti or sug_to:
        run.tokens_input = (run.tokens_input or 0) + sug_ti
        run.tokens_output = (run.tokens_output or 0) + sug_to
        session.add(run)
        session.commit()

    return ok({
        "type": "answer",
        "run_id": run.id,
        "session_id": session_id,
        "dataset_ids": sandbox_ids,
        "derived_dataset_ids": derived_dataset_ids,
        "datasets_used": datasets_used,
        "selector_reasoning": run.selector_reasoning,
        "answer_markdown": answer_md,
        "answer_html": render_markdown(answer_md),
        "iteration_count": run.iteration_count,
        "tokens_input": run.tokens_input,
        "tokens_output": run.tokens_output,
        "status": run.status,
        "is_best_effort": is_best_effort,
        "steps": steps,
        "suggested_questions": suggested_questions,
    })
