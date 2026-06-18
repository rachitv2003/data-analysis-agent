import json
import structlog

from data_analyst.graph.agent import agent_graph
from data_analyst.graph.state import AgentState
from data_analyst.db.session import create_db_session, init_db
from data_analyst.db.models import QueryRunRow, ConversationSessionRow

logger = structlog.get_logger()

MAX_SESSION_TURNS = 20


def run_agent(
    dataset_ids: list[str],
    question: str,
    session_id: str | None = None,
    sandbox_dataset_ids: list[str] | None = None,
    selector_reasoning: str | None = None,
) -> tuple[str, str]:
    """Create a QueryRun, invoke the ReAct agent, return (run_id, session_id).

    dataset_ids       — full set of session datasets; used for C14 constraint + session storage.
    sandbox_dataset_ids — C19 selector subset to load into the sandbox; defaults to dataset_ids.
    selector_reasoning  — raw LLM output from the C19 selector call (stored on the run).
    """
    init_db()

    if not dataset_ids:
        raise ValueError("At least one dataset_id is required")

    effective_sandbox_ids = sandbox_dataset_ids if sandbox_dataset_ids else dataset_ids

    primary_dataset_id = dataset_ids[0]
    dataset_ids_json = json.dumps(dataset_ids) if len(dataset_ids) > 1 else None
    conversation_history: list[dict] = []

    with create_db_session() as session:
        if session_id:
            sess_row = session.get(ConversationSessionRow, session_id)
            if sess_row is None:
                raise ValueError(f"Session {session_id} not found")

            # Validate dataset set matches
            sess_ids = (
                json.loads(sess_row.dataset_ids_json)
                if sess_row.dataset_ids_json
                else [sess_row.dataset_id]
            )
            if sorted(sess_ids) != sorted(dataset_ids):
                raise ValueError("Session dataset mismatch")

            prior_runs = (
                session.query(QueryRunRow)
                .filter(
                    QueryRunRow.session_id == session_id,
                    QueryRunRow.status == "completed",
                )
                .order_by(QueryRunRow.created_at)
                .all()
            )
            if len(prior_runs) >= MAX_SESSION_TURNS:
                raise ValueError(f"Session limit reached ({MAX_SESSION_TURNS} turns); start a new session")

            conversation_history = [
                {"question": r.question, "answer": r.answer}
                for r in prior_runs
                if r.answer
            ]
        else:
            sess_row = ConversationSessionRow(
                dataset_id=primary_dataset_id,
                dataset_ids_json=dataset_ids_json,
            )
            session.add(sess_row)
            session.flush()
            session_id = sess_row.id

        run = QueryRunRow(
            dataset_id=primary_dataset_id,
            dataset_ids_json=dataset_ids_json,
            session_id=session_id,
            question=question,
            status="running",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    initial: AgentState = {
        "run_id": run_id,
        "dataset_ids": effective_sandbox_ids,
        "session_id": session_id,
        "question": question,
        "conversation_history": conversation_history,
        "action_history": [],
        "iteration_count": 0,
        "error": None,
        "selector_reasoning": selector_reasoning,
    }

    logger.info("agent.start", run_id=run_id, dataset_ids=dataset_ids, session_id=session_id)
    final = agent_graph.invoke(initial)
    logger.info("agent.done", run_id=run_id, status=final.get("status"))

    return run_id, session_id
