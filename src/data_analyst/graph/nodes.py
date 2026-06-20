import json
import re
import structlog
import pandas as pd
from pathlib import Path

from data_analyst.graph.state import AgentState
from data_analyst.llm.providers.factory import create_llm_client
from data_analyst.llm.client import LLMClient

logger = structlog.get_logger()

# Per-run DataFrame store: run_id → {var_name: DataFrame}
_dataframes: dict[str, dict[str, pd.DataFrame]] = {}
_llm_client: LLMClient | None = None
_llm_provider_name: str = "stub"

# C27 session-scoped DataFrame cache: session_id → {dataset_id: DataFrame}
_session_cache: dict[str, dict[str, pd.DataFrame]] = {}
# LRU tracking: list of (session_id, dataset_id) newest-last
_cache_lru: list[tuple[str, str]] = []
_cache_bytes: int = 0


def _df_bytes(df: pd.DataFrame) -> int:
    try:
        return int(df.memory_usage(deep=True).sum())
    except Exception:
        return 0


def _cache_limit_bytes() -> int:
    try:
        from data_analyst.config.settings import get_settings
        return get_settings().cache_limit_mb * 1024 * 1024
    except Exception:
        return 1024 * 1024 * 1024


def _touch_cache(session_id: str, dataset_id: str) -> None:
    """Move (session_id, dataset_id) to the end (most-recently-used) of LRU list."""
    key = (session_id, dataset_id)
    try:
        _cache_lru.remove(key)
    except ValueError:
        pass
    _cache_lru.append(key)


def _store_in_cache(session_id: str, dataset_id: str, df: pd.DataFrame) -> None:
    global _cache_bytes
    if session_id not in _session_cache:
        _session_cache[session_id] = {}
    nbytes = _df_bytes(df)
    _session_cache[session_id][dataset_id] = df
    _cache_bytes += nbytes
    _touch_cache(session_id, dataset_id)
    # Evict oldest entries until under the limit
    limit = _cache_limit_bytes()
    while _cache_bytes > limit and _cache_lru:
        oldest_session, oldest_dataset = _cache_lru.pop(0)
        evicted = _session_cache.get(oldest_session, {}).pop(oldest_dataset, None)
        if evicted is not None:
            _cache_bytes -= _df_bytes(evicted)
        if oldest_session in _session_cache and not _session_cache[oldest_session]:
            del _session_cache[oldest_session]


def _evict_session(session_id: str) -> None:
    """Evict all cached DataFrames for a session (called on session delete)."""
    global _cache_bytes
    if session_id not in _session_cache:
        return
    for df in _session_cache[session_id].values():
        _cache_bytes -= _df_bytes(df)
    del _session_cache[session_id]
    _cache_lru[:] = [(s, d) for s, d in _cache_lru if s != session_id]
    logger.debug("cache.evict_session", session_id=session_id)


def _invalidate_dataset(dataset_id: str) -> None:
    """Remove a dataset from the cache across all sessions (called on clean/delete)."""
    global _cache_bytes
    for session_id in list(_session_cache.keys()):
        df = _session_cache[session_id].pop(dataset_id, None)
        if df is not None:
            _cache_bytes -= _df_bytes(df)
    _cache_lru[:] = [(s, d) for s, d in _cache_lru if d != dataset_id]
    logger.debug("cache.invalidate_dataset", dataset_id=dataset_id)

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

_LIBRARIES_INSTRUCTION = (
    "Pre-bound variables available in every code block (no imports needed):\n"
    "- `pd` — pandas\n"
    "- `np` — numpy\n"
    "- `px` — plotly.express\n"
    "- `go` — plotly.graph_objects\n"
    "- `plt` — matplotlib.pyplot\n"
    "- `sns` — seaborn\n"
    "- `scipy` — scipy (also `stats` for scipy.stats)\n"
    "- `sklearn` — scikit-learn (import specific submodules as needed, e.g. "
    "`from sklearn.cluster import KMeans`)\n"
    "- `sm` — statsmodels.api\n"
    "You may still use `import` for submodules (e.g. `from sklearn.preprocessing import StandardScaler`), "
    "but the top-level aliases above are already available.\n"
)

_SAVE_DATASET_INSTRUCTION = (
    "Derived dataset persistence:\n"
    "- `save_dataset(df, name, description='')` — persists a DataFrame as a named dataset in the "
    "database so it survives beyond this session.\n"
    "- Call it whenever you create a merged, joined, cleaned, or aggregated DataFrame that is the "
    "main subject of your analysis. Do not wait for the user to ask — if you built a meaningful "
    "derived table, save it so they can query it in future sessions.\n"
    "- Call it in its own code block: `save_dataset(result_df, 'merged_products', 'Products joined with category translations')`\n"
    "- The last expression in that block must be the save_dataset call (its return value is the result).\n"
    "- Do NOT call save_dataset for chart-only data, single-value summaries, or one-liner filters "
    "that can be trivially reproduced.\n"
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

    # Persistent agent memory
    memory_text = ""
    try:
        from data_analyst.db.session import create_db_session
        from data_analyst.db.models import SettingsRow
        with create_db_session() as _db:
            _mem = _db.get(SettingsRow, "global_memory")
            if _mem and _mem.value and _mem.value.strip():
                memory_text = _mem.value.strip()
    except Exception:
        pass
    memory_block = (
        f"Persistent memory (always treat as authoritative background knowledge):\n{memory_text}\n\n"
        if memory_text else ""
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

    # C25: derived datasets manifest — session-scoped
    derived_lines: list[str] = []
    session_id_for_manifest = state.get("session_id")
    try:
        from data_analyst.db.session import create_db_session
        from data_analyst.db.models import DatasetRow, QueryRunRow
        with create_db_session() as _db:
            if session_id_for_manifest:
                # Collect run IDs belonging to this session
                session_run_ids = {
                    r.id for r in _db.query(QueryRunRow).filter(
                        QueryRunRow.session_id == session_id_for_manifest
                    ).all()
                }
                derived = (
                    _db.query(DatasetRow)
                    .filter(
                        DatasetRow.derived_from_run_id.isnot(None),
                        DatasetRow.origin == "derived",
                    )
                    .all()
                )
                derived = [
                    d for d in derived
                    if d.derived_from_run_id in session_run_ids
                ]
            else:
                derived = []
            for d in derived:
                derived_lines.append(
                    f"- `{_var_name(d.filename)}` ({d.filename}): "
                    f"{d.row_count} rows × {d.col_count} cols — "
                    f"{d.context or 'no description'}"
                )
    except Exception:
        pass
    derived_block = (
        "Previously saved derived datasets (also available as variables if in sandbox):\n"
        + "\n".join(derived_lines) + "\n\n"
        if derived_lines else ""
    )

    return (
        f"<node:plan>\n"
        f"You are a data analysis assistant.\n"
        f"{df_description}\n\n"
        f"{derived_block}"
        f"{context_block}"
        f"{memory_block}"
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
        f"CRITICAL — output format (strictly one of these two, never mixed):\n"
        f"  A) If you have enough information to answer: your ENTIRE response must be exactly "
        f"'FINAL ANSWER: <your answer here>' — nothing before it, no code, no preamble.\n"
        f"  B) If you need to compute something: your ENTIRE response must be Python code only "
        f"— no 'FINAL ANSWER:', no explanatory prose, no markdown fences.\n"
        f"  Mixing code and 'FINAL ANSWER:' in the same response will cause a syntax error. "
        f"Never do it.\n\n"
        f"Instructions:\n"
        f"- Once you have enough information, respond with: FINAL ANSWER: <your answer>\n"
        f"- Your FINAL ANSWER must always contain substantive content — never leave it blank.\n"
        f"- {_MARKDOWN_INSTRUCTION}"
        f"{_CHART_INSTRUCTION}"
        f"{_LIBRARIES_INSTRUCTION}"
        f"{_SAVE_DATASET_INSTRUCTION}"
        f"- If you still need data or need to produce a chart, respond with a Python code block "
        f"(one or more lines). The last line must be an expression whose value is the result "
        f"(a DataFrame, Series, scalar, or a Plotly `fig` object). Do NOT use print(). "
        f"Do NOT wrap the code in markdown fences. Use the variable names shown above.\n"
    )


def setup(state: AgentState) -> AgentState:
    from data_analyst.db.session import create_db_session
    from data_analyst.db.models import DatasetRow

    run_id = state["run_id"]
    session_id = state.get("session_id")
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
                if var in df_map:
                    var = f"{var}_{i}"

                # C27: check session cache first
                cached = (
                    _session_cache.get(session_id, {}).get(did)
                    if session_id else None
                )
                if cached is not None:
                    df = cached.copy()
                    _touch_cache(session_id, did)
                    logger.debug("setup.cache_hit", session_id=session_id, dataset_id=did)
                else:
                    # Load from Parquet if available, else CSV
                    if row.parquet_path and Path(row.parquet_path).exists():
                        df = pd.read_parquet(row.parquet_path, engine="pyarrow")
                        logger.debug("setup.parquet_load", dataset_id=did)
                    else:
                        df = pd.read_csv(row.file_path)

                    if session_id:
                        _store_in_cache(session_id, did, df)

                df_map[var] = df
                if row.context:
                    prefix = f"[{row.filename}]" if len(dataset_ids) > 1 else ""
                    context_parts.append(f"{prefix} {row.context}".strip())

        # Always provide df / df1 / df2 / … aliases
        final_map: dict[str, pd.DataFrame] = {}
        for i, (var, df) in enumerate(df_map.items(), 1):
            final_map[var] = df
            final_map[f"df{i}"] = df
        if final_map:
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


def _make_eval_ns(
    df_map: dict,
    run_id: str = "",
    session_id: str | None = None,
    dataset_ids: list[str] | None = None,
) -> tuple[dict, list[str]]:
    """Build the code evaluation namespace.

    Returns (ns, code_ref) where code_ref is a one-element list.
    Set code_ref[0] to the current expression before calling _exec_code so
    the save_dataset closure can record the derivation code.
    """
    code_ref: list[str] = [""]

    ns: dict = {**df_map, "pd": pd}

    # C25: save_dataset closure
    def save_dataset(df: pd.DataFrame, name: str, description: str = "") -> str:
        if not isinstance(df, pd.DataFrame):
            raise TypeError("save_dataset: first argument must be a pandas DataFrame")
        if not name or not name.strip():
            raise ValueError("save_dataset: name must be a non-empty string")

        name = name.strip()
        derivation_code = code_ref[0]

        try:
            import json as _json
            from data_analyst.config.settings import get_settings
            from data_analyst.db.session import create_db_session
            from data_analyst.db.models import DatasetRow
            from data_analyst.utils.file_parser import compute_hash

            settings = get_settings()
            upload_dir = Path(settings.upload_dir)
            upload_dir.mkdir(exist_ok=True)

            parent_ids_json = _json.dumps(dataset_ids or [])

            with create_db_session() as _db:
                new_row = DatasetRow(
                    filename=f"{name}.csv",
                    file_path="",
                    row_count=len(df),
                    col_count=len(df.columns),
                    columns_json=_json.dumps(df.columns.tolist()),
                    content_hash=compute_hash(df.to_csv(index=False).encode()),
                    format="csv",
                    context=description.strip() or None,
                    origin="derived",
                    derived_from_run_id=run_id or None,
                    derived_from_dataset_ids=parent_ids_json,
                    derivation_code=derivation_code or None,
                )
                _db.add(new_row)
                _db.flush()
                new_id = new_row.id

                # Write CSV
                csv_path = upload_dir / f"{new_id}.csv"
                df.to_csv(csv_path, index=False)
                new_row.file_path = str(csv_path.resolve())

                # Write Parquet (non-fatal)
                try:
                    parquet_path = upload_dir / f"{new_id}.parquet"
                    df.to_parquet(parquet_path, engine="pyarrow", index=False)
                    new_row.parquet_path = str(parquet_path.resolve())
                except Exception:
                    pass

            # Inject into runtime namespace + run-level df_map for subsequent code blocks
            var = _var_name(name)
            df_map[var] = df
            if run_id and run_id in _dataframes:
                _dataframes[run_id][var] = df
            ns[var] = df

            # Store in session cache
            if session_id:
                _store_in_cache(session_id, new_id, df)

            logger.info(
                "save_dataset.ok",
                name=name,
                dataset_id=new_id,
                rows=len(df),
                session_id=session_id,
            )
            return (
                f"Dataset '{name}' saved — {len(df)} rows × {len(df.columns)} cols "
                f"(id: {new_id}). Variable `{var}` now available."
            )
        except Exception as exc:
            logger.error("save_dataset.error", name=name, error=str(exc))
            raise RuntimeError(f"save_dataset failed: {exc}") from exc

    ns["save_dataset"] = save_dataset

    try:
        import numpy as np
        ns["np"] = np
    except ImportError:
        pass
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
    try:
        import seaborn as sns
        ns["sns"] = sns
    except ImportError:
        pass
    try:
        import scipy
        ns["scipy"] = scipy
        import scipy.stats as stats
        ns["stats"] = stats
    except ImportError:
        pass
    try:
        import sklearn
        ns["sklearn"] = sklearn
    except ImportError:
        pass
    try:
        import statsmodels.api as sm
        ns["sm"] = sm
    except ImportError:
        pass
    return ns, code_ref


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

    # Safety net: strip any trailing "FINAL ANSWER: ..." the LLM mixed into the code block
    mixed_match = re.search(r"\n\s*FINAL ANSWER\s*:", expression, re.IGNORECASE)
    if mixed_match:
        expression = expression[:mixed_match.start()].strip()

    eval_ns, code_ref = _make_eval_ns(
        df_map,
        run_id=run_id,
        session_id=state.get("session_id"),
        dataset_ids=state.get("dataset_ids"),
    )
    code_ref[0] = expression

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

    # Find FINAL ANSWER: anywhere in the response — LLM sometimes embeds it after code
    idx = raw.upper().find("FINAL ANSWER:")
    if idx != -1:
        answer_md = raw[idx + len("FINAL ANSWER:"):].strip()
    else:
        answer_md = raw.strip()

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


def generate_suggestions(question: str, answer: str) -> tuple[list[str], int, int]:
    """Generate 3 follow-up question suggestions after an answer.

    Returns (suggestions, tokens_input, tokens_output).
    """
    prompt = (
        "You are a helpful data analysis assistant. Based on the question and answer below, "
        "generate exactly 3 short follow-up questions the user might want to ask next. "
        "Return ONLY a JSON array of 3 strings, no other text.\n\n"
        f"Question: {question}\n\n"
        f"Answer (summary): {answer[:600]}"
    )
    try:
        llm = _get_llm()
        resp = llm.complete(prompt)
        text = resp.text.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(q).strip() for q in parsed[:3] if q], resp.tokens_input, resp.tokens_output
    except Exception:
        pass
    return [], 0, 0


def handle_error(state: AgentState) -> AgentState:
    run_id = state["run_id"]
    error = state.get("error", "Unknown error")
    error_answer = f"_Sorry, I was unable to answer this question._\n\n**Reason:** {error}"

    _dataframes.pop(run_id, None)
    _persist_run(run_id, state, error_answer, "failed", error)
    logger.error("handle_error.done", run_id=run_id, error=error)
    return {**state, "answer": error_answer, "status": "failed"}
