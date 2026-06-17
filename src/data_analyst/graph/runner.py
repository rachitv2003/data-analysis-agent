import structlog

from data_analyst.graph.agent import agent_graph
from data_analyst.graph.state import AgentState
from data_analyst.db.session import create_db_session, init_db
from data_analyst.db.models import QueryRunRow

logger = structlog.get_logger()


def run_agent(dataset_id: str, question: str) -> str:
    """Create a QueryRun record, invoke the agent, return the run_id."""
    init_db()

    with create_db_session() as session:
        run = QueryRunRow(
            dataset_id=dataset_id,
            question=question,
            status="running",
        )
        session.add(run)
        session.flush()
        run_id = run.id

    initial: AgentState = {
        "run_id": run_id,
        "dataset_id": dataset_id,
        "question": question,
        "action_history": [],
        "iteration_count": 0,
        "error": None,
    }

    logger.info("agent.start", run_id=run_id, dataset_id=dataset_id)
    final = agent_graph.invoke(initial)
    logger.info("agent.done", run_id=run_id, status=final.get("status"))

    return run_id
