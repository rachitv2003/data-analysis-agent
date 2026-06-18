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
    "`go` (plotly.graph_objects) — both are pre-imported.\n"
    "- Return `fig` as the LAST expression in your code block (just the bare variable, not a call). "
    "Do NOT call fig.to_html(), fig.show(), or fig.write_html().\n"
    "- The system captures the figure automatically and renders it as an interactive chart.\n"
    "- When you see '[Chart N captured: ...]' in an action result, the chart was saved. "
    "Write your FINAL ANSWER next — do NOT run more chart actions unless the user asked for multiple charts.\n"
    "- For multiple charts, each chart is a separate action. After the last chart action, write FINAL ANSWER.\n"
    "- Do NOT use matplotlib. Keep titles concise (≤ 60 chars). Always set axis labels.\n"
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
    """Convert filename to a safe Python variable name: 'Sales Data.csv' → 'sales_data'."""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    base = re.sub(r"[^\w]", "_", stem.lower()).strip("_")
    return re.sub(r"_+", "_", base)


_ALIAS_RE = re.compile(r"^df\d*$")


def _build_prompt(state: AgentState) -> str:
    full_map = _dataframes.get(state["run_id"], {})

    # Only show original filename-derived vars in schema; skip df/df1/df2 aliases
    original = [(var, df) for var, df in full_map.items() if not _ALIAS_RE.match(var)]

    schema_lines = []
    for var, df in original:
        schema_lines.append(
            f"- `{var}`: {len(df)} rows × {len(df.columns)} cols — "
            f"columns: {', '.join(df.columns.tolist()[:20])}"
        )

    if len(original) == 1:
        var, df = original[0]
        df_description = (
            f"The DataFrame is available as `{var}` (also aliased as `df`):\n"
            + "\n".join(schema_lines)
        )
    else:
        df_description = (
            f"Available DataFrames — reference each by its variable name:\n"
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

    # Action history this turn — truncate very long results to avoid token bloat.
    action_lines = []
    for entry in state.get("action_history", []):
        prefix = "Error" if entry.get("is_error") else "Result"
        result = entry["result"]
        if isinstance(result, str) and len(result) > 1500:
            result = result[:1500] + f"… [truncated — {len(result):,} chars total]"
        action_lines.append(f"Action: {entry['action']}\n{prefix}: {result}")
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
        f"- If you still need data or need to produce a chart, respond with a Python code block "
        f"(one or more lines). The last line must be an expression whose value is the result "
        f"(a DataFrame, Series, scalar, or a Plotly `fig` object). Do NOT use print(). "
        f"Do NOT wrap the code in markdown fences. Use the variable names shown above.\n"
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

        logger.info("setup.loaded", run_id=run_id, datasets=len(dataset_ids))
        return {
            **state,
            "action_history": [],
            "charts": [],
            "iteration_count": 0,
            "tokens_input": 0,
            "tokens_output": 0,
            "dataset_context": combined_context,
        }
    except Exception as exc:
        logger.error("setup.error", run_id=run_id, error=str(exc))
        return {**state, "error": str(exc), "status": "failed"}


_WRAPUP_INSTRUCTION = (
    "IMPORTANT: You are running out of iterations. "
    "You MUST produce a FINAL ANSWER in this response or the next one. "
    "Summarise your best findings from the action history above, even if incomplete. "
    "Do not start a new line of investigation."
)


def plan_action(state: AgentState) -> AgentState:
    from data_analyst.config.settings import get_settings

    run_id = state["run_id"]
    iteration_count = state.get("iteration_count", 0)
    max_iter = get_settings().max_iterations

    try:
        prompt = _build_prompt(state)
        if iteration_count >= max_iter - 2:
            prompt = prompt + f"\n{_WRAPUP_INSTRUCTION}\n"
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


def _exec_code(code: str, ns: dict):
    """Execute one or more lines of Python; return the value of the last expression."""
    lines = code.strip().splitlines()
    if not lines:
        return None

    # Split preamble (all but last line) from the final expression
    preamble = "\n".join(lines[:-1])
    last = lines[-1].strip()

    if preamble:
        with pd.option_context(
            "display.max_rows", _MAX_ROWS,
            "display.max_columns", _MAX_COLS,
            "display.width", None,
            "display.max_colwidth", 100,
        ):
            exec(preamble, ns)  # noqa: S102

    # Try to eval the last line as an expression (returns a value)
    try:
        with pd.option_context(
            "display.max_rows", _MAX_ROWS,
            "display.max_columns", _MAX_COLS,
            "display.width", None,
            "display.max_colwidth", 100,
        ):
            return eval(last, ns)  # noqa: S307
    except SyntaxError:
        # Last line is a statement (e.g. assignment) — exec it, return None
        exec(last, ns)  # noqa: S102
        return None


def _update_iteration_count(run_id: str, iteration_count: int) -> None:
    """C22: write iteration_count to DB mid-run so the progress endpoint reflects live state."""
    try:
        from data_analyst.db.session import create_db_session
        from data_analyst.db.models import QueryRunRow
        with create_db_session() as db:
            run = db.get(QueryRunRow, run_id)
            if run:
                run.iteration_count = iteration_count
                db.commit()
    except Exception:
        pass


def execute_action(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    _update_iteration_count(run_id, state.get("iteration_count", 0))
    expression = state.get("llm_response", "").strip()
    df_map = _dataframes.get(run_id, {})

    if not df_map:
        return {**state, "error": "DataFrame not found in cache", "status": "failed"}

    history = list(state.get("action_history", []))
    charts = list(state.get("charts", []))

    # Strip markdown code fences the LLM sometimes wraps code in
    expression = re.sub(r"^```[a-zA-Z]*\n?", "", expression)
    expression = re.sub(r"\n?```$", "", expression).strip()

    eval_ns = _make_eval_ns(df_map)

    try:
        result = _exec_code(expression, eval_ns)

        # C4: detect Plotly figure — capture as JSON spec instead of HTML
        try:
            import plotly.basedatatypes as _pbt
            if isinstance(result, _pbt.BaseFigure):
                charts.append(result.to_json())
                n = len(charts)
                try:
                    title = result.layout.title.text or f"chart {n}"
                except Exception:
                    title = f"chart {n}"
                result_str = f"[Chart {n} captured: {title}]"
                logger.info("execute_action.chart_captured", run_id=run_id, n=n, title=title)
                history.append({"action": expression, "result": result_str, "is_error": False})
                return {**state, "action_history": history, "charts": charts}
        except ImportError:
            pass

        # Detect fig.to_html() output — model called the wrong method; treat as error so it retries
        if isinstance(result, str) and "Plotly.newPlot" in result:
            err_str = (
                "Error: fig.to_html() output detected. "
                "Return `fig` as the last expression — never call .to_html(), .show(), or .write_html()."
            )
            logger.warning("execute_action.plotly_html_detected", run_id=run_id)
            history.append({"action": expression, "result": err_str, "is_error": True})
            return {**state, "action_history": history, "charts": charts}

        result_str = _result_to_str(result)
        logger.info("execute_action.ok", run_id=run_id, expr_preview=expression[:60])
        history.append({"action": expression, "result": result_str, "is_error": False})
        return {**state, "action_history": history, "charts": charts}
    except Exception as exc:
        err_str = str(exc)
        logger.warning("execute_action.error", run_id=run_id, error=err_str)
        history.append({"action": expression, "result": err_str, "is_error": True})
        return {**state, "action_history": history, "charts": charts}


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
                run.selector_reasoning = state.get("selector_reasoning")
    except Exception as exc:
        logger.error("persist_run.error", run_id=run_id, error=str(exc))


def _append_charts(answer_md: str, state: AgentState) -> str:
    """Append any captured Plotly chart divs to the answer markdown."""
    import json as _json
    import html as _html

    charts = state.get("charts", [])
    if not charts:
        return answer_md
    chart_divs = []
    for chart_json in charts:
        spec = _json.loads(chart_json)
        compact = {"data": spec.get("data", []), "layout": spec.get("layout", {})}
        attr = _html.escape(_json.dumps(compact), quote=True)
        chart_divs.append(f'<div class="plotly-chart" data-spec="{attr}"></div>')
    return (answer_md + "\n\n" if answer_md.strip() else "") + "\n".join(chart_divs)


def finalize(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    raw = state.get("llm_response", "")

    answer_md = raw
    for prefix in ("FINAL ANSWER:", "final answer:"):
        if answer_md.strip().lower().startswith(prefix.lower()):
            answer_md = answer_md.strip()[len(prefix):].strip()
            break

    answer_md = _append_charts(answer_md, state)
    _dataframes.pop(run_id, None)
    _persist_run(run_id, state, answer_md, "completed")
    logger.info("finalize.done", run_id=run_id, charts=len(state.get("charts", [])))
    return {**state, "answer": answer_md, "status": "completed"}


def force_finalize(state: AgentState) -> AgentState:
    """C20: Best-effort synthesis when max iterations or consecutive errors reached."""
    run_id = state["run_id"]

    # Determine reason
    history = state.get("action_history", [])
    if len(history) >= 3 and all(h.get("is_error") for h in history[-3:]):
        reason = "consecutive_errors"
    else:
        reason = "max_iterations"

    # Build synthesis prompt
    action_lines = []
    for entry in history:
        prefix = "Error" if entry.get("is_error") else "Result"
        result = entry["result"]
        if isinstance(result, str) and len(result) > 800:
            result = result[:800] + "… [truncated]"
        action_lines.append(f"Action: {entry['action']}\n{prefix}: {result}")
    history_text = "\n\n".join(action_lines) if action_lines else "No actions were executed."

    prompt = (
        f"<node:finalize>\n"
        f"The analysis loop has ended. Based on the work done so far, write the best answer you can.\n"
        f"If you have partial results, summarise them. If no useful results were obtained, explain "
        f"what you tried and what information would be needed to answer properly.\n"
        f"Do NOT say 'I was unable to answer' without explanation. Always produce substantive content.\n"
        f"Format your response using Markdown.\n"
        f"</node:finalize>\n\n"
        f"Question: {state.get('question', '')}\n\n"
        f"Work done so far:\n{history_text}"
    )

    try:
        llm = _get_llm()
        resp = llm.complete(prompt)
        answer_md = resp.text.strip()
        tokens_in = state.get("tokens_input", 0) + resp.tokens_input
        tokens_out = state.get("tokens_output", 0) + resp.tokens_output
        logger.warning("force_finalize.done", run_id=run_id, reason=reason)
    except Exception as exc:
        logger.error("force_finalize.llm_error", run_id=run_id, error=str(exc))
        answer_md = (
            "_Analysis ended early. Here is what was attempted:_\n\n"
            + "\n".join(f"- {e['action']}" for e in history if not e.get("is_error"))
            or "_No successful operations were completed._"
        )
        tokens_in = state.get("tokens_input", 0)
        tokens_out = state.get("tokens_output", 0)

    answer_md = _append_charts(answer_md, state)
    updated = {
        **state,
        "tokens_input": tokens_in,
        "tokens_output": tokens_out,
    }
    _dataframes.pop(run_id, None)
    _persist_run(run_id, updated, answer_md, "completed", error=reason)
    return {**updated, "answer": answer_md, "status": "completed"}


def handle_error(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    error = state.get("error", "Unknown error")
    error_answer = f"_Sorry, I was unable to answer this question._\n\n**Reason:** {error}"

    _dataframes.pop(run_id, None)
    _persist_run(run_id, state, error_answer, "failed", error)
    logger.error("handle_error.done", run_id=run_id, error=error)
    return {**state, "answer": error_answer, "status": "failed"}
