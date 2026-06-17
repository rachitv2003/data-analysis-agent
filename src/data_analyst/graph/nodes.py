import json
import re
import structlog
import pandas as pd

from data_analyst.graph.state import AgentState
from data_analyst.llm.providers.factory import create_llm_client
from data_analyst.llm.client import LLMClient

logger = structlog.get_logger()

# Per-run DataFrame store: run_id → {var_name: DataFrame}
_dataframes: dict[str, dict[str, pd.DataFrame]] = {}
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

_CHART_INSTRUCTION = (
    "Chart / visualisation instructions:\n"
    "- When the user asks for a chart, graph, plot, bar chart, line chart, scatter plot, "
    "histogram, pie chart, heatmap, or any visualisation, use `px` (plotly.express) or "
    "`go` (plotly.graph_objects) — both are pre-imported in the sandbox.\n"
    "- Produce the chart HTML with `fig.to_html(include_plotlyjs='cdn')` as the final "
    "expression in your action. The system will adjust the CDN flag automatically — always "
    "write `'cdn'` yourself.\n"
    "- Do NOT use matplotlib unless plotly raises an ImportError.\n"
    "- In the FINAL ANSWER, embed the raw HTML string returned by fig.to_html() directly — "
    "do NOT wrap it in a code block or Markdown fence.\n"
    "- For a dashboard (multiple charts), produce each chart as a separate action, then in "
    "the FINAL ANSWER concatenate the HTML strings, each on its own line.\n"
    "- Keep chart titles concise (≤ 60 characters). Always set axis labels.\n"
)

_MAX_ROWS = 100
_MAX_COLS = 20


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


def _var_name(filename: str) -> str:
    """Convert filename to a safe Python variable name: 'Sales Data.csv' → 'sales_data_csv'."""
    base = re.sub(r"[^\w]", "_", filename.lower()).strip("_")
    return re.sub(r"_+", "_", base)


_ALIAS_RE = re.compile(r"^df\d*$")


def _build_prompt(state: AgentState) -> str:
    full_map = _dataframes.get(state["run_id"], {})

    # Only show original filename-derived vars in schema; skip df/df1/df2 aliases
    original = [(var, df) for var, df in full_map.items() if not _ALIAS_RE.match(var)]

    schema_lines = []
    for i, (var, df) in enumerate(original, 1):
        schema_lines.append(
            f"- df{i} ({var}): {len(df)} rows × {len(df.columns)} cols — "
            f"columns: {', '.join(df.columns.tolist()[:20])}"
        )

    if len(original) == 1:
        var, df = original[0]
        df_description = (
            f"The DataFrame is available as `df` / `df1` / `{var}`:\n"
            + "\n".join(schema_lines)
        )
    else:
        df_description = (
            f"Available DataFrames — use df1, df2, … or the variable names shown:\n"
            + "\n".join(schema_lines)
        )

    # Dataset context (C12)
    ctx = state.get("dataset_context") or ""
    context_block = (
        f"Dataset context (treat as authoritative):\n{ctx}\n\n"
        if ctx else ""
    )

    # Prior conversation
    conv_history = state.get("conversation_history", [])
    conv_lines = [f"Q: {t['question']}\nA: {t['answer']}" for t in conv_history[-10:]]
    conv_text = "\n\n".join(conv_lines) if conv_lines else ""
    prior_context = f"Previous conversation:\n{conv_text}\n\n" if conv_text else ""

    # Action history this turn
    action_lines = []
    for entry in state.get("action_history", []):
        prefix = "Error" if entry.get("is_error") else "Result"
        action_lines.append(f"Action: {entry['action']}\n{prefix}: {entry['result']}")
    history_text = "\n\n".join(action_lines) if action_lines else "None yet."

    return (
        f"<node:plan>\n"
        f"You are a data analysis assistant.\n"
        f"{df_description}\n\n"
        f"{context_block}"
        f"IMPORTANT — question interpretation:\n"
        f"- The user's question may contain typos or informal phrasing. Interpret it charitably.\n"
        f"- Questions like 'what can you tell me about this file', 'describe the data', 'summarise' are "
        f"conversational — answer them directly with FINAL ANSWER using what you already know from the schema. "
        f"Do NOT execute pandas for schema-level questions.\n"
        f"- Only execute a pandas expression when you need a value not available from the schema alone "
        f"(e.g. a specific aggregation, filter, or join).\n\n"
        f"{prior_context}"
        f"Current question: {state['question']}\n\n"
        f"Action history (this turn):\n{history_text}\n\n"
        f"Instructions:\n"
        f"- Once you have enough information, respond with: FINAL ANSWER: <your answer>\n"
        f"- Your FINAL ANSWER must always contain substantive content — never leave it blank.\n"
        f"- {_MARKDOWN_INSTRUCTION}"
        f"{_CHART_INSTRUCTION}"
        f"- If you still need data, respond with ONLY a single pandas/plotly expression. "
        f"Use df1/df2/… or the variable names listed above. Do NOT use print().\n"
    )


def setup(state: AgentState) -> AgentState:
    from data_analyst.db.session import create_db_session
    from data_analyst.db.models import DatasetRow

    run_id = state["run_id"]
    dataset_ids = state.get("dataset_ids", [])

    try:
        df_map: dict[str, pd.DataFrame] = {}
        context_parts: list[str] = []

        with create_db_session() as session:
            for i, did in enumerate(dataset_ids, 1):
                row = session.get(DatasetRow, did)
                if row is None:
                    return {**state, "error": f"Dataset {did} not found", "status": "failed"}
                var = _var_name(row.filename)
                # Ensure unique var names (e.g. two files with same name after sanitising)
                if var in df_map:
                    var = f"{var}_{i}"
                df = pd.read_csv(row.file_path)
                df_map[var] = df
                if row.context:
                    prefix = f"[{row.filename}]" if len(dataset_ids) > 1 else ""
                    context_parts.append(f"{prefix} {row.context}".strip())

        # Always provide df / df1 / df2 / … aliases
        final_map: dict[str, pd.DataFrame] = {}
        for i, (var, df) in enumerate(df_map.items(), 1):
            final_map[var] = df
            final_map[f"df{i}"] = df
        if len(final_map) > 0:
            first_df = next(iter(df_map.values()))
            final_map["df"] = first_df

        _dataframes[run_id] = final_map
        combined_context = "\n\n".join(context_parts) or None

        # C4: detect whether Plotly CDN JS was already sent in an earlier turn of this session
        plotly_js_loaded = False
        session_id = state.get("session_id")
        if session_id:
            from data_analyst.db.models import QueryRunRow as _QRR
            prior_runs = (
                session.query(_QRR)
                .filter(_QRR.session_id == session_id, _QRR.status == "completed")
                .all()
            )
            plotly_js_loaded = any(
                r.answer and "cdn.plot.ly" in r.answer for r in prior_runs
            )

        logger.info("setup.loaded", run_id=run_id, datasets=len(dataset_ids))
        return {
            **state,
            "action_history": [],
            "iteration_count": 0,
            "tokens_input": 0,
            "tokens_output": 0,
            "dataset_context": combined_context,
            "plotly_js_loaded": plotly_js_loaded,
        }
    except Exception as exc:
        logger.error("setup.error", run_id=run_id, error=str(exc))
        return {**state, "error": str(exc), "status": "failed"}


def plan_action(state: AgentState) -> AgentState:
    from data_analyst.config.settings import get_settings

    run_id = state["run_id"]
    iteration_count = state.get("iteration_count", 0)
    max_iter = get_settings().max_iterations

    if iteration_count >= max_iter:
        logger.warning("plan_action.max_iterations", run_id=run_id)
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


def _result_to_str(result) -> str:
    try:
        if isinstance(result, pd.DataFrame):
            total_rows, total_cols = len(result), len(result.columns)
            truncated = result.iloc[:_MAX_ROWS, :_MAX_COLS]
            md = truncated.to_markdown(index=True)
            notes = []
            if total_rows > _MAX_ROWS:
                notes.append(f"showing {_MAX_ROWS} of {total_rows} rows")
            if total_cols > _MAX_COLS:
                notes.append(f"showing {_MAX_COLS} of {total_cols} columns")
            return md + (f"\n\n_({', '.join(notes)})_" if notes else "")
        if isinstance(result, pd.Series):
            total = len(result)
            md = result.head(_MAX_ROWS).to_markdown()
            note = f"\n\n_(showing {_MAX_ROWS} of {total} rows)_" if total > _MAX_ROWS else ""
            return md + note
    except Exception:
        pass
    return str(result)


def _make_eval_ns(df_map: dict) -> dict:
    ns: dict = {**df_map, "pd": pd}
    try:
        import plotly.express as px
        import plotly.graph_objects as go
        ns["px"] = px
        ns["go"] = go
    except ImportError:
        pass
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        ns["plt"] = plt
    except ImportError:
        pass
    return ns


def execute_action(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    expression = state.get("llm_response", "").strip()
    df_map = _dataframes.get(run_id, {})

    if not df_map:
        return {**state, "error": "DataFrame not found in cache", "status": "failed"}

    history = list(state.get("action_history", []))
    plotly_js_loaded = state.get("plotly_js_loaded", False)

    # C4: dedup Plotly CDN — if JS already loaded this session, swap 'cdn' → False
    if plotly_js_loaded and "include_plotlyjs='cdn'" in expression:
        expression = expression.replace("include_plotlyjs='cdn'", "include_plotlyjs=False")
    if plotly_js_loaded and 'include_plotlyjs="cdn"' in expression:
        expression = expression.replace('include_plotlyjs="cdn"', "include_plotlyjs=False")

    eval_ns = _make_eval_ns(df_map)

    try:
        with pd.option_context(
            "display.max_rows", _MAX_ROWS,
            "display.max_columns", _MAX_COLS,
            "display.width", None,
            "display.max_colwidth", 100,
        ):
            result = eval(expression, eval_ns)  # noqa: S307
        result_str = _result_to_str(result)
        logger.info("execute_action.ok", run_id=run_id, expr_preview=expression[:60])
        history.append({"action": expression, "result": result_str, "is_error": False})
        # Mark CDN as loaded if this result includes the Plotly CDN script
        if isinstance(result_str, str) and "cdn.plot.ly" in result_str:
            plotly_js_loaded = True
        return {**state, "action_history": history, "plotly_js_loaded": plotly_js_loaded}
    except Exception as exc:
        err_str = str(exc)
        logger.warning("execute_action.error", run_id=run_id, error=err_str)
        history.append({"action": expression, "result": err_str, "is_error": True})
        return {**state, "action_history": history}


def _persist_run(run_id: str, state: AgentState, answer: str, status: str, error: str | None = None) -> None:
    from data_analyst.db.session import create_db_session
    from data_analyst.db.models import QueryRunRow
    import json as _json

    try:
        with create_db_session() as db:
            run = db.get(QueryRunRow, run_id)
            if run:
                run.answer = answer
                run.status = status
                if error:
                    run.error_message = error
                run.action_history = _json.dumps(state.get("action_history", []))
                run.iteration_count = state.get("iteration_count", 0)
                run.tokens_input = state.get("tokens_input", 0)
                run.tokens_output = state.get("tokens_output", 0)
                ids = state.get("dataset_ids", [])
                if len(ids) > 1:
                    run.dataset_ids_json = _json.dumps(ids)
    except Exception as exc:
        logger.error("persist_run.error", run_id=run_id, error=str(exc))


def finalize(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    raw = state.get("llm_response", "")

    answer_md = raw
    for prefix in ("FINAL ANSWER:", "final answer:"):
        if answer_md.strip().lower().startswith(prefix.lower()):
            answer_md = answer_md.strip()[len(prefix):].strip()
            break

    _dataframes.pop(run_id, None)
    _persist_run(run_id, state, answer_md, "completed")
    logger.info("finalize.done", run_id=run_id)
    return {**state, "answer": answer_md, "status": "completed"}


def handle_error(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    error = state.get("error", "Unknown error")
    error_answer = f"_Sorry, I was unable to answer this question._\n\n**Reason:** {error}"

    _dataframes.pop(run_id, None)
    _persist_run(run_id, state, error_answer, "failed", error)
    logger.error("handle_error.done", run_id=run_id, error=error)
    return {**state, "answer": error_answer, "status": "failed"}
