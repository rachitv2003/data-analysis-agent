"""C19: Lightweight pre-flight dataset selector.

Runs before the ReAct graph is invoked. Makes a single non-iterative LLM call
to choose which datasets are relevant to the user's question.
"""
import json
import structlog

logger = structlog.get_logger()

_PROMPT_TEMPLATE = """\
<node:select>
You are a dataset selector. Given the user's question and the list of available datasets,
return a JSON array of dataset IDs (and only those IDs) that are needed to answer the question.
Return [] if none are relevant (the caller will handle the fallback).
Do not include any explanation outside the JSON array.
</node:select>

Question: {question}

Available datasets:
{schema_block}

Response (JSON array of IDs only):"""


def _build_schema_block(datasets) -> str:
    lines = []
    for i, ds in enumerate(datasets, 1):
        try:
            cols = json.loads(ds.columns_json)
            col_str = ", ".join(cols)
        except Exception:
            col_str = "(unknown columns)"
        lines.append(
            f'Dataset {i} — "{ds.filename}" (id: {ds.id}): '
            f"{ds.row_count} rows, {ds.col_count} columns — {col_str}"
        )
    return "\n".join(lines)


def select_datasets(question: str, datasets: list) -> tuple[list[str], str | None]:
    """Return (selected_ids, raw_reasoning).

    selected_ids — subset of dataset IDs to load into the sandbox.
    raw_reasoning — raw LLM response stored as selector_reasoning on the run.

    Falls back to all IDs on empty selection, malformed JSON, or any exception.
    """
    all_ids = [ds.id for ds in datasets]

    if not datasets:
        return all_ids, None

    if len(datasets) == 1:
        return all_ids, None

    from data_analyst.llm.providers.factory import create_llm_client

    schema_block = _build_schema_block(datasets)
    prompt = _PROMPT_TEMPLATE.format(question=question, schema_block=schema_block)

    try:
        provider, _ = create_llm_client()
        response = provider.complete(prompt)
        raw = response.text.strip()
    except Exception as exc:
        logger.warning("selector.llm_error", error=str(exc))
        return all_ids, None

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            valid = [id_ for id_ in parsed if id_ in all_ids]
            if valid:
                logger.info("selector.selected", count=len(valid), total=len(all_ids))
                return valid, raw
        logger.warning("selector.empty_or_invalid", raw=raw[:200])
        return all_ids, raw
    except json.JSONDecodeError:
        logger.warning("selector.malformed_json", raw=raw[:200])
        return all_ids, raw
