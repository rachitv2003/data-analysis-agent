import json
import structlog
import pandas as pd

from data_analyst.graph.state import AgentState
from data_analyst.llm.providers.factory import create_llm_client
from data_analyst.llm.client import LLMClient

logger = structlog.get_logger()

_dataframes: dict[str, pd.DataFrame] = {}
_llm_client: LLMClient | None = None
_llm_provider_name: str = "stub"

_MARKDOWN_INSTRUCTION = (
    "Format your FINAL ANSWER using Markdown:\n"
    "- Use **bold** for key numbers and column names\n"
    "- Use a Markdown table if the result is tabular\n"
    "- Use bullet lists where appropriate\n"
    "- Use ## headings only if the answer has multiple distinct sections\n"
    "- Do NOT wrap prose in code blocks\n"
)


def _get_llm() -> LLMClient:
    global _llm_client, _llm_provider_name
    if _llm_client is None:
        provider, name = create_llm_client()
        _llm_client = LLMClient(provider)
        _llm_provider_name = name
    return _llm_client


def get_provider_name() -> str:
    _get_llm()
    return _llm_provider_name


def _build_prompt(state: AgentState) -> str:
    conv_history = state.get("conversation_history", [])
    conv_lines = [f"Q: {t['question']}\nA: {t['answer']}" for t in conv_history[-10:]]
    conv_text = "\n\n".join(conv_lines) if conv_lines else ""

    action_lines = []
    for entry in state.get("action_history", []):
        prefix = "Error" if entry.get("is_error") else "Result"
        action_lines.append(f"Action: {entry['action']}\n{prefix}: {entry['result']}")
    history_text = "\n\n".join(action_lines) if action_lines else "None yet."

    prior_context = f"Previous conversation:\n{conv_text}\n\n" if conv_text else ""

    return (
        f"<node:plan>\n"
        f"You are a data analysis assistant. Answer the user's question using pandas expressions.\n"
        f"The DataFrame is available as `df`.\n\n"
        f"{prior_context}"
        f"Current question: {state['question']}\n\n"
        f"Action history (this turn):\n{history_text}\n\n"
        f"Instructions:\n"
        f"- If you can now answer definitively, respond with: FINAL ANSWER: <your answer>\n"
        f"- {_MARKDOWN_INSTRUCTION}"
        f"- Otherwise respond with ONLY a single pandas expression (no explanation, no markdown).\n"
        f"- Do NOT use print(). Just the expression.\n"
    )


def setup(state: AgentState) -> AgentState:
    from data_analyst.db.session import create_db_session
    from data_analyst.db.models import DatasetRow

    run_id = state["run_id"]
    dataset_id = state["dataset_id"]

    try:
        with create_db_session() as session:
            row = session.get(DatasetRow, dataset_id)
            if row is None:
                return {**state, "error": f"Dataset {dataset_id} not found", "status": "failed"}
            file_path = row.file_path

        df = pd.read_csv(file_path)
        _dataframes[run_id] = df
        logger.info("setup.loaded", run_id=run_id, shape=df.shape)
        return {**state, "action_history": [], "iteration_count": 0, "tokens_input": 0, "tokens_output": 0}
    except Exception as exc:
        logger.error("setup.error", run_id=run_id, error=str(exc))
        return {**state, "error": str(exc), "status": "failed"}


def plan_action(state: AgentState) -> AgentState:
    from data_analyst.config.settings import get_settings

    run_id = state["run_id"]
    iteration_count = state.get("iteration_count", 0)
    max_iter = get_settings().max_iterations

    if iteration_count >= max_iter:
        logger.warning("plan_action.max_iterations", run_id=run_id, iteration_count=iteration_count)
        return {**state, "error": f"Max iterations ({max_iter}) reached.", "status": "failed"}

    try:
        prompt = _build_prompt(state)
        llm = _get_llm()
        resp = llm.complete(prompt)
        logger.info("plan_action.response", run_id=run_id, iteration=iteration_count, preview=resp.text[:80])
        return {
            **state,
            "llm_response": resp.text,
            "iteration_count": iteration_count + 1,
            "tokens_input": state.get("tokens_input", 0) + resp.tokens_input,
            "tokens_output": state.get("tokens_output", 0) + resp.tokens_output,
        }
    except Exception as exc:
        logger.error("plan_action.error", run_id=run_id, error=str(exc))
        return {**state, "error": str(exc), "status": "failed"}


def execute_action(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    expression = state.get("llm_response", "").strip()
    df = _dataframes.get(run_id)

    if df is None:
        return {**state, "error": "DataFrame not found in cache", "status": "failed"}

    history = list(state.get("action_history", []))
    try:
        result = eval(expression, {"df": df, "pd": pd})  # noqa: S307
        result_str = str(result)
        logger.info("execute_action.ok", run_id=run_id, expr_preview=expression[:60])
        history.append({"action": expression, "result": result_str, "is_error": False})
        return {**state, "action_history": history}
    except Exception as exc:
        err_str = str(exc)
        logger.warning("execute_action.error", run_id=run_id, error=err_str)
        history.append({"action": expression, "result": err_str, "is_error": True})
        return {**state, "action_history": history}


def finalize(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    raw = state.get("llm_response", "")

    answer_md = raw
    for prefix in ("FINAL ANSWER:", "final answer:"):
        if answer_md.strip().lower().startswith(prefix.lower()):
            answer_md = answer_md.strip()[len(prefix):].strip()
            break

    _dataframes.pop(run_id, None)

    from data_analyst.db.session import create_db_session
    from data_analyst.db.models import QueryRunRow

    try:
        with create_db_session() as session:
            run = session.get(QueryRunRow, run_id)
            if run:
                run.answer = answer_md
                run.status = "completed"
                run.action_history = json.dumps(state.get("action_history", []))
                run.iteration_count = state.get("iteration_count", 0)
                run.tokens_input = state.get("tokens_input", 0)
                run.tokens_output = state.get("tokens_output", 0)
    except Exception as exc:
        logger.error("finalize.db_error", run_id=run_id, error=str(exc))

    logger.info("finalize.done", run_id=run_id)
    return {**state, "answer": answer_md, "status": "completed"}


def handle_error(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    error = state.get("error", "Unknown error")

    _dataframes.pop(run_id, None)

    from data_analyst.db.session import create_db_session
    from data_analyst.db.models import QueryRunRow

    try:
        with create_db_session() as session:
            run = session.get(QueryRunRow, run_id)
            if run:
                run.status = "failed"
                run.error_message = error
                run.action_history = json.dumps(state.get("action_history", []))
                run.iteration_count = state.get("iteration_count", 0)
                run.tokens_input = state.get("tokens_input", 0)
                run.tokens_output = state.get("tokens_output", 0)
    except Exception as exc:
        logger.error("handle_error.db_error", run_id=run_id, error=str(exc))

    logger.error("handle_error.done", run_id=run_id, error=error)
    return {**state, "status": "failed"}
