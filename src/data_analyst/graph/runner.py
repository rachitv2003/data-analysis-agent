import structlog

from data_analyst.graph.agent import agent_graph
from data_analyst.graph.state import AgentState
from data_analyst.db.session import create_db_session, init_db
from data_analyst.db.models import QueryRunRow, ConversationSessionRow

logger = structlog.get_logger()

MAX_SESSION_TURNS = 20


def run_agent(dataset_id: str, question: str, session_id: str | None = None) -> str:
    """Create a QueryRun, invoke the ReAct agent, return the run_id."""
    init_db()

    conversation_history: list[dict] = []

    with create_db_session() as session:
        # Resolve or create conversation session
        if session_id:
            sess_row = session.get(ConversationSessionRow, session_id)
            if sess_row is None:
                raise ValueError(f"Session {session_id} not found")
            if sess_row.dataset_id != dataset_id:
                raise ValueError("Session dataset mismatch")

            # Load prior turns
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
            # Start a new session
            sess_row = ConversationSessionRow(dataset_id=dataset_id)
            session.add(sess_row)
            session.flush()
            session_id = sess_row.id

        run = QueryRunRow(
            dataset_id=dataset_id,
            session_id=session_id,
            question=question,
            status="running",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    initial: AgentState = {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "session_id": session_id,
        "question": question,
        "conversation_history": conversation_history,
        "action_history": [],
        "iteration_count": 0,
        "error": None,
    }

    logger.info("agent.start", run_id=run_id, dataset_id=dataset_id, session_id=session_id)
    final = agent_graph.invoke(initial)
    logger.info("agent.done", run_id=run_id, status=final.get("status"))

    return run_id, session_id
