"""ReAct loop nodes (LangGraph).

Replaces the skeleton's `transform_text` graph. The six nodes implement the
Reason+Act loop from `spec/agent.md` -> "## Nodes / Steps":

    setup -> plan_action -> execute_action -> (loop) -> finalize / force_finalize
                                            -> handle_error (fatal)

All LLM calls go through `LLMClient` (never a provider SDK directly). The DataFrames
for a run live in the module-level `_dataframes` registry, keyed by `run_id`
(Phase 2 is the simple per-run load — no session cache; that lands in Phase 3).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

import pandas as pd

from db.models import DatasetRow, QueryRunRow
from db.session import create_db_session
from graph.cancellation import is_cancelled  # cooperative Stop
from graph.compress import extract_facts  # C31: distil notes -> compact facts
from graph.memory import get_memory_block  # owned by slice-3c; 3b only imports it
from graph.sandbox import _safe_alias, build_namespace, eval_expression, make_save_dataset
from graph.state import AgentState
from llm.client import LLMClient
from observability.events import get_logger

logger = get_logger("graph.nodes")

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"
_PLAN_PROMPT_PATH = _PROMPTS_DIR / "plan_action.md"
_FINALIZE_PROMPT_PATH = _PROMPTS_DIR / "finalize.md"

# Run-scoped DataFrame registry: {run_id: {"frames": [df, ...], "filenames": [...]}}.
# Phase 2 single-turn loads here and releases on finalize/error. Phase 3 adds the
# session-keyed cache below (C27) for multi-turn sessions.
_dataframes: dict[str, dict[str, Any]] = {}

# Run-scoped registry of derived dataset ids created by `save_dataset` during a
# run (C25), keyed by `run_id` — mirrors `_dataframes`. The runner reads this via
# `get_derived_created(run_id)` to surface `derived_dataset_ids` in the payload,
# and it is cleared when the run's DataFrames are released.
_derived_created: dict[str, list[str]] = {}

# Session DataFrame cache (C27): {session_id: {"frames": {dataset_id: df},
#   "order": [dataset_id, ...]}}. Reused across turns of the same conversation so
# a multi-turn session does not re-read Parquet/CSV every turn. Small LRU bound
# on the number of cached sessions (insertion order = LRU order; touch on hit).
_session_cache: dict[str, dict[str, Any]] = {}
_SESSION_CACHE_MAX = 8

# Node tags injected so the stub provider can branch deterministically.
_PLAN_TAG = "<node:plan>"
_FINALIZE_TAG = "<node:finalize>"

# How many consecutive execution errors force a wrap-up.
_MAX_CONSECUTIVE_ERRORS = 3


def _load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _estimate_tokens(text: str) -> int:
    """Rough token estimate when the provider does not report usage.

    ~4 chars/token is the documented heuristic (see spec/agent.md plan_action).
    """
    return max(1, len(text or "") // 4)


def _uploads_dir() -> Path:
    """`uploads/` at the repo root (two levels up from src/graph/)."""
    return Path(__file__).resolve().parent.parent.parent / "uploads"


def _load_dataframe(dataset_id: str) -> pd.DataFrame:
    """Load a dataset's DataFrame: Parquet preferred, CSV fallback.

    Raises on a missing/unreadable file (fatal -> handle_error).
    """
    base = _uploads_dir()
    parquet_path = base / f"{dataset_id}.parquet"
    csv_path = base / f"{dataset_id}.csv"
    if parquet_path.exists():
        return pd.read_parquet(parquet_path)
    if csv_path.exists():
        return pd.read_csv(csv_path)
    raise FileNotFoundError(
        f"No data file for dataset {dataset_id!r} (looked for {parquet_path.name} / {csv_path.name})"
    )


def _schema_block(df: pd.DataFrame, name: str, notes: str | None = None) -> str:
    """A compact column-schema description for the prompt's dataset context."""
    cols = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns)
    lines = [f"Dataset `{name}`: {df.shape[0]} rows x {df.shape[1]} cols", f"Columns: {cols}"]
    if notes:
        lines.append(f"Notes: {notes.strip()}")
    return "\n".join(lines)


def _facts_as_notes(facts: list[str]) -> str:
    """Render C31 compressed facts as a compact notes string for the prompt."""
    return "; ".join(f.strip() for f in facts if f and str(f).strip())


def _trigger_facts_self_heal(dataset_id: str) -> None:
    """Fire-and-forget C31 extraction for a dataset that has notes but no compressed
    facts yet, so future plan prompts use the smaller facts (raw notes are used this
    turn). Never blocks planning and never raises; `extract_facts` is in-flight-locked
    so repeated triggers don't double-run.
    """

    def _run() -> None:
        try:
            extract_facts(dataset_id)
        except Exception as exc:  # noqa: BLE001 — self-heal is best-effort
            logger.warning("facts_self_heal_failed", dataset_id=dataset_id, error=str(exc))

    try:
        threading.Thread(
            target=_run, name=f"c31-heal-{dataset_id[:8]}", daemon=True
        ).start()
    except Exception as exc:  # noqa: BLE001 — never let self-heal crash planning
        logger.warning("facts_self_heal_spawn_failed", dataset_id=dataset_id, error=str(exc))


def _dataset_notes_for_prompt(
    dataset_id: str, context: str | None, facts: list[str] | None
) -> str:
    """C31: prefer the compressed facts (smaller prompt) over the raw notes, falling
    back to the raw notes — and lazily self-healing — when a dataset has notes but no
    facts yet. This is what makes C31 actually shrink the plan prompt.
    """
    clean_facts = [f for f in (facts or []) if f and str(f).strip()]
    if clean_facts:
        return _facts_as_notes(clean_facts)
    raw = (context or "").strip()
    if raw:
        _trigger_facts_self_heal(dataset_id)
    return raw


def _safe_memory_block() -> str:
    """Read the global persistent memory block (slice-3c), defensively.

    A 3c regression (import/runtime failure) must NEVER crash planning, so any
    exception yields an empty block — planning simply proceeds without memory.
    """
    try:
        return (get_memory_block() or "").strip()
    except Exception as exc:  # noqa: BLE001 — memory is best-effort context
        logger.warning("memory_block_failed", error=str(exc))
        return ""


# --------------------------------------------------------------------------- #
# Session DataFrame cache (C27)
# --------------------------------------------------------------------------- #


def _session_load_frame(session_id: str, dataset_id: str) -> pd.DataFrame:
    """Return the DataFrame for `dataset_id` within `session_id`, using the cache.

    On a cache HIT: reuse the stored DataFrame and LRU-touch the session. On a
    MISS: load via `_load_dataframe`, store it, and bound the cache to
    `_SESSION_CACHE_MAX` sessions (evict the oldest on overflow).
    """
    entry = _session_cache.get(session_id)
    if entry is None:
        entry = {"frames": {}, "order": []}
        _session_cache[session_id] = entry

    # LRU-touch the session (move to most-recent).
    _session_cache.pop(session_id, None)
    _session_cache[session_id] = entry

    frames: dict[str, pd.DataFrame] = entry["frames"]
    if dataset_id in frames:
        logger.info("session_cache_hit", session_id=session_id, dataset_id=dataset_id)
        return frames[dataset_id]

    frame = _load_dataframe(dataset_id)
    frames[dataset_id] = frame
    if dataset_id not in entry["order"]:
        entry["order"].append(dataset_id)
    logger.info("session_cache_miss", session_id=session_id, dataset_id=dataset_id)

    # Bound the number of cached sessions (evict the oldest).
    while len(_session_cache) > _SESSION_CACHE_MAX:
        oldest = next(iter(_session_cache))
        _session_cache.pop(oldest, None)
        logger.info("session_cache_evict", session_id=oldest)

    return frame


# --------------------------------------------------------------------------- #
# Nodes
# --------------------------------------------------------------------------- #


def setup(state: AgentState) -> AgentState:
    """Load DataFrame(s) for the run and build the dataset context.

    When `session_id` is set (multi-turn), DataFrames come from the session cache
    (C27) — hit reuses + LRU-touches, miss loads Parquet/CSV and stores. Without
    a `session_id` (single-turn), the simple per-run `_dataframes[run_id]` path is
    used unchanged. The frames list stays ordered by `dataset_ids`. A fatal
    load/lookup error sets `error` -> handle_error.
    """
    run_id = state.get("run_id", "")
    session_id = state.get("session_id")
    dataset_ids = state.get("dataset_ids") or []

    if not dataset_ids:
        return {**state, "error": "No datasets supplied to load."}

    frames: list[pd.DataFrame] = []
    filenames: list[str] = []
    schema_parts: list[str] = []

    try:
        with create_db_session() as session:
            for dataset_id in dataset_ids:
                row = session.get(DatasetRow, dataset_id)
                if row is None:
                    return {**state, "error": f"Dataset {dataset_id!r} not found."}
                if session_id:
                    frame = _session_load_frame(session_id, dataset_id)
                else:
                    frame = _load_dataframe(dataset_id)
                frames.append(frame)
                filenames.append(row.filename or f"{dataset_id}.csv")
                schema_parts.append(
                    _schema_block(
                        frame,
                        row.filename or dataset_id,
                        _dataset_notes_for_prompt(
                            dataset_id, row.context, row.context_facts
                        ),
                    )
                )
    except Exception as exc:  # noqa: BLE001 — fatal load error
        logger.warning("setup_load_failed", run_id=run_id, error=str(exc))
        return {**state, "error": f"Failed to load dataset(s): {exc}"}

    # The run-scoped registry is what execute_action reads each step; populate it
    # for BOTH paths (the session cache is the source of frames, this is the
    # per-run handle). The frames list stays ordered by `dataset_ids`.
    _dataframes[run_id] = {"frames": frames, "filenames": filenames}
    dataset_context = "\n\n".join(schema_parts)
    logger.info("setup_ok", run_id=run_id, datasets=len(frames), session=bool(session_id))
    return {**state, "dataset_context": dataset_context, "status": "running"}


def _assemble_plan_prompt(state: AgentState, wrap_up: bool) -> str:
    """Build the plan_action user prompt from question + transcript + context."""
    parts: list[str] = [_PLAN_TAG]

    dataset_context = state.get("dataset_context")
    if dataset_context:
        parts.append("## Datasets\n" + dataset_context)

    # Global persistent memory (slice-3c) is authoritative in every plan prompt,
    # injected only when non-empty. Read defensively so a 3c failure can't crash
    # planning.
    memory_block = _safe_memory_block()
    if memory_block:
        parts.append("## Persistent memory\n" + memory_block)

    conversation_history = state.get("conversation_history") or []
    if conversation_history:
        convo = "\n".join(
            f"Q: {turn.get('question', '')}\nA: {turn.get('answer', '')}"
            for turn in conversation_history
        )
        parts.append("## Earlier in this conversation\n" + convo)

    parts.append("## Question\n" + (state.get("question") or ""))

    action_history = state.get("action_history") or []
    if action_history:
        transcript_lines: list[str] = []
        for step in action_history:
            transcript_lines.append(f"Action: {step.get('action', '')}")
            if step.get("is_error"):
                transcript_lines.append(f"Error: {step.get('result', '')}")
            else:
                transcript_lines.append(f"Result: {step.get('result', '')}")
        parts.append("## Actions so far\n" + "\n".join(transcript_lines))

    if wrap_up:
        parts.append(
            "## Wrap up now\n"
            "You are running low on steps. Do NOT run another action. Reply with "
            "`FINAL ANSWER:` and your best Markdown answer from the results above."
        )

    parts.append(
        "Now reply with EITHER a single pandas expression to run next — just the "
        "raw code, with NO backticks, NO ```python fence, and NO surrounding prose "
        "— OR `FINAL ANSWER:` followed by your answer."
    )
    return "\n\n".join(parts)


def plan_action(state: AgentState) -> AgentState:
    """Ask the LLM for the next pandas action or a FINAL ANSWER."""
    # User pressed Stop: skip the planning LLM call; after_plan routes to
    # force_finalize (which also short-circuits) so the run wraps up with no
    # further model calls.
    if is_cancelled(state.get("run_id", "")):
        return {**state, "llm_response": ""}

    iteration = state.get("iteration_count", 0)
    max_iterations = state.get("max_iterations", 6)
    wrap_up = iteration >= max_iterations - 2

    try:
        system = _load_prompt(_PLAN_PROMPT_PATH)
        prompt = _assemble_plan_prompt(state, wrap_up)
        resp = LLMClient().complete(prompt, system=system)
    except Exception as exc:  # noqa: BLE001 — fatal LLM error
        logger.warning("plan_action_failed", run_id=state.get("run_id"), error=str(exc))
        return {**state, "error": f"LLM call failed: {exc}"}

    reply = resp.text or ""
    # Prefer the provider's REAL token usage; fall back to the chars/4 estimate
    # only when a provider reports none (e.g. usage metadata missing).
    in_toks = resp.tokens_input or (_estimate_tokens(prompt) + _estimate_tokens(system))
    out_toks = resp.tokens_output or _estimate_tokens(reply)
    tokens_input = state.get("tokens_input", 0) + in_toks
    tokens_output = state.get("tokens_output", 0) + out_toks

    logger.info(
        "plan_action",
        run_id=state.get("run_id"),
        iteration=iteration + 1,
        is_final=("final answer:" in reply.lower()),
    )
    return {
        **state,
        "llm_response": reply,
        "iteration_count": iteration + 1,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
    }


# A ```python … ``` (or bare ``` … ```) fenced code block. Some models wrap the
# action in a fence — often after a few lines of reasoning prose — instead of the
# bare expression the prompt asks for.
_CODE_FENCE_RE = re.compile(r"```[ \t]*(?:python|py)?[ \t]*\r?\n?(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_code(text: str) -> str:
    """Pull the runnable pandas code out of a planner reply.

    The plan prompt asks for a single BARE expression, and capable models comply
    (so the reply IS the code). But some models (e.g. gemini-2.5-flash-lite) add
    reasoning prose and wrap the code in a ```python fence. Passing that prose to
    eval() raises spurious SyntaxErrors — an apostrophe in "turn's" reads as an
    unterminated string literal — so the model's actual code never runs.

    When the reply contains fenced block(s) we return the LAST non-empty one (the
    step's intended action); otherwise the stripped reply is used as-is. This keeps
    the executor model-agnostic without constraining well-behaved models.
    """
    if not text:
        return ""
    blocks = _CODE_FENCE_RE.findall(text)
    for block in reversed(blocks):
        if block.strip():
            return _strip_inline_ticks(block.strip())
    return _strip_inline_ticks(text.strip())


def _strip_inline_ticks(s: str) -> str:
    """Strip inline-code backticks a model wrapped a bare expression in (`expr`).

    Some models emit the action as inline code — e.g. `` `df.columns.tolist()` `` —
    which is not valid Python. When the whole (single-line) reply is fenced in
    backticks we peel them off; multi-line text is left alone (a real code block,
    handled by the fence regex, or genuine content).
    """
    s = s.strip()
    if "\n" not in s and s.startswith("`") and s.endswith("`"):
        return s.strip("`").strip()
    return s


def execute_action(state: AgentState) -> AgentState:
    """Eval the model's pandas expression in the sandbox; record the step.

    On exception: mark `is_error=true`, record the error, route back to plan_action
    (recoverable). Charts are captured into `charts`. `iteration_count` is written
    to the DB each step for live polling.

    The planner reply is run through `_extract_code` first, so a model that wraps
    its action in a ```python fence (with reasoning prose around it) still has its
    REAL code executed instead of eval() choking on the prose.
    """
    run_id = state.get("run_id", "")
    expr = _extract_code(state.get("llm_response", ""))
    action_history = list(state.get("action_history") or [])
    charts = list(state.get("charts") or [])

    bundle = _dataframes.get(run_id, {})
    frames = bundle.get("frames", [])
    filenames = bundle.get("filenames", [])
    namespace = build_namespace(frames, filenames)

    # Override the module-level stub with a run-aware `save_dataset` that knows the
    # producing run_id + parent dataset_ids and records each created derived id
    # (C25). The closure captures the current action expression as derivation_code
    # via `eval_expression` (it calls `.set_code` before eval).
    parent_ids = list(state.get("dataset_ids") or [])

    def _on_registered(new_id: str) -> None:
        _derived_created.setdefault(run_id, []).append(new_id)

    def _on_saved(name: str, df: pd.DataFrame) -> None:
        # Make a save_dataset'd frame referenceable as `<name>` (v0.5 parity):
        # append it to THIS run's frames so the NEXT step's build_namespace aliases
        # it, and inject it into the CURRENT action's namespace so a later
        # statement in the same action can use it. A re-save of the same name
        # replaces the prior frame (latest wins).
        alias = _safe_alias(name) or name
        b = _dataframes.get(run_id)
        if b is not None:
            fn = f"{name}.csv"
            try:
                i = b["filenames"].index(fn)
                b["frames"][i] = df
            except ValueError:
                b["frames"].append(df)
                b["filenames"].append(fn)
        namespace[alias] = df

    namespace["save_dataset"] = make_save_dataset(
        run_id=run_id,
        parent_ids=parent_ids,
        on_registered=_on_registered,
        on_saved=_on_saved,
    )

    result_str, new_charts, is_error, error_str = eval_expression(expr, namespace)
    if new_charts:
        charts.extend(new_charts)

    step = {
        "action": expr,
        "result": error_str if is_error else result_str,
        "is_error": is_error,
    }
    action_history.append(step)

    # Persist iteration_count each step for live progress polling (best-effort).
    try:
        with create_db_session() as session:
            row = session.get(QueryRunRow, run_id)
            if row is not None:
                row.iteration_count = state.get("iteration_count", 0)
                row.action_history = action_history
    except Exception as exc:  # noqa: BLE001 — polling write is non-fatal
        logger.warning("execute_action_db_write_failed", run_id=run_id, error=str(exc))

    logger.info("execute_action", run_id=run_id, is_error=is_error)
    return {**state, "action_history": action_history, "charts": charts}


def _strip_final_answer(text: str) -> str:
    """Strip a leading `FINAL ANSWER:` prefix (case-insensitive, tolerate preamble)."""
    if not text:
        return ""
    lower = text.lower()
    marker = "final answer:"
    idx = lower.find(marker)
    if idx == -1:
        return text.strip()
    return text[idx + len(marker):].strip()


def get_derived_created(run_id: str) -> list[str]:
    """Derived dataset ids created by `save_dataset` during `run_id` (C25).

    Read by the runner to surface `derived_dataset_ids` in the `/ask` payload.
    Returns a fresh copy; empty when nothing was saved (or already released).
    """
    return list(_derived_created.get(run_id, []))


def release_derived_created(run_id: str) -> None:
    """Drop the run's derived-id registry once the runner has read it."""
    _derived_created.pop(run_id, None)


def _release_dataframe(run_id: str) -> None:
    _dataframes.pop(run_id, None)


def invalidate_dataset_cache(dataset_id: str) -> None:
    """Evict `dataset_id` from every session entry in `_session_cache` (C27/D2).

    Called after a dataset is mutated (clean/apply or re-derive) so subsequent
    multi-turn turns see the fresh on-disk data rather than the stale cached
    DataFrame. Empty session entries are removed to keep the cache compact.
    """
    stale_sessions = [
        sid for sid, entry in _session_cache.items()
        if dataset_id in entry.get("frames", {})
    ]
    for sid in stale_sessions:
        entry = _session_cache.get(sid)
        if entry is None:
            continue
        frames: dict = entry.get("frames", {})
        frames.pop(dataset_id, None)
        order: list = entry.get("order", [])
        try:
            order.remove(dataset_id)
        except ValueError:
            pass
        # If the session now has no cached frames, drop the session entry entirely.
        if not frames:
            _session_cache.pop(sid, None)
            logger.info("session_cache_evict_empty", session_id=sid)
        else:
            logger.info(
                "session_cache_invalidated",
                session_id=sid,
                dataset_id=dataset_id,
            )


def finalize(state: AgentState) -> AgentState:
    """Produce the final answer from the model's FINAL ANSWER reply."""
    run_id = state.get("run_id", "")
    answer = _strip_final_answer(state.get("llm_response", ""))
    _release_dataframe(run_id)
    logger.info("finalize", run_id=run_id, answer_len=len(answer))
    return {**state, "answer": answer, "status": "completed"}


def _build_transcript(action_history: list[dict]) -> str:
    lines: list[str] = []
    for step in action_history:
        lines.append(f"Action: {step.get('action', '')}")
        label = "Error" if step.get("is_error") else "Result"
        lines.append(f"{label}: {step.get('result', '')}")
    return "\n".join(lines)


def force_finalize(state: AgentState) -> AgentState:
    """Best-effort synthesis when the loop hits max-iter or consecutive errors.

    ONE synthesis LLM call; `status` is ALWAYS `completed`. Falls back to a static
    message if the call fails. Sets an informational `error_message`.
    """
    run_id = state.get("run_id", "")
    action_history = state.get("action_history") or []

    # User pressed Stop: wrap up WITHOUT a synthesis LLM call — that's the whole
    # point of stopping. Return a short marker; the run persists as completed.
    if is_cancelled(run_id):
        _release_dataframe(run_id)
        logger.info("force_finalize_cancelled", run_id=run_id)
        return {
            **state,
            "answer": "_Run stopped by the user before it finished._",
            "status": "completed",
            "error_message": "cancelled",
        }

    # Classify why we are wrapping up (informational, not a failure). Consecutive
    # errors take precedence over max-iter when both could apply.
    consecutive = 0
    for step in reversed(action_history):
        if step.get("is_error"):
            consecutive += 1
        else:
            break
    reason = "consecutive_errors" if consecutive >= _MAX_CONSECUTIVE_ERRORS else "max_iterations"

    try:
        system = _load_prompt(_FINALIZE_PROMPT_PATH)
        prompt = (
            f"{_FINALIZE_TAG}\n\n"
            f"## Question\n{state.get('question', '')}\n\n"
            f"## Transcript\n{_build_transcript(action_history)}\n\n"
            "Write the best-effort final answer in Markdown (no FINAL ANSWER: prefix)."
        )
        resp = LLMClient().complete(prompt, system=system)
        answer = (resp.text or "").strip() or _static_best_effort(state)
        in_toks = resp.tokens_input or (_estimate_tokens(prompt) + _estimate_tokens(system))
        out_toks = resp.tokens_output or _estimate_tokens(answer)
        tokens_input = state.get("tokens_input", 0) + in_toks
        tokens_output = state.get("tokens_output", 0) + out_toks
    except Exception as exc:  # noqa: BLE001 — fall back to a static message
        logger.warning("force_finalize_llm_failed", run_id=run_id, error=str(exc))
        answer = _static_best_effort(state)
        tokens_input = state.get("tokens_input", 0)
        tokens_output = state.get("tokens_output", 0)

    _release_dataframe(run_id)
    logger.info("force_finalize", run_id=run_id, reason=reason)
    return {
        **state,
        "answer": answer,
        "status": "completed",
        "error_message": reason,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
    }


def _static_best_effort(state: AgentState) -> str:
    return (
        "I was not able to fully complete the analysis within the available steps. "
        "Based on the work done so far, here is a best-effort summary; please try "
        "rephrasing or narrowing the question for a more precise answer."
    )


def handle_error(state: AgentState) -> AgentState:
    """Fatal error: mark the run failed, keep the message, release the DataFrame."""
    run_id = state.get("run_id", "")
    _release_dataframe(run_id)
    logger.warning("handle_error", run_id=run_id, error=state.get("error"))
    return {**state, "status": "failed", "error_message": state.get("error")}
