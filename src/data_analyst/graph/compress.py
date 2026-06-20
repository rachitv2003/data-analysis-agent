"""C31: Semantic context compression — LLM structured fact extraction."""
import json
import re
import structlog

logger = structlog.get_logger()

_EXTRACT_PROMPT = (
    "Extract the key analytical facts from the following text as a JSON array of strings.\n"
    "Each element must be one atomic, self-contained fact written as a concise statement.\n"
    "Do not include formatting notes, column type definitions that are already obvious from "
    "the data schema, or redundant phrasing.\n"
    "Maximum 20 facts. Return ONLY a valid JSON array — no markdown fences, no other text.\n\n"
    "Text:\n{text}"
)


def extract_facts(text: str) -> list[str]:
    """Call LLM to extract key facts from text. Returns empty list on any failure."""
    if not text or not text.strip():
        return []
    try:
        from data_analyst.graph.nodes import _get_llm
        llm = _get_llm()
        resp = llm.complete(_EXTRACT_PROMPT.format(text=text.strip()))
        raw = resp.text.strip()
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(f).strip() for f in parsed if f and str(f).strip()]
    except Exception as exc:
        logger.warning("compress.extract_facts.error", error=str(exc))
    return []


def compress_dataset_context(dataset_id: str) -> None:
    """Background task: extract facts from DatasetRow.context → context_facts."""
    try:
        from data_analyst.db.session import create_db_session
        from data_analyst.db.models import DatasetRow
        with create_db_session() as db:
            row = db.get(DatasetRow, dataset_id)
            if row is None or not row.context:
                return
            facts = extract_facts(row.context)
            if facts:
                row.context_facts = json.dumps(facts)
                logger.info("compress.dataset.ok", dataset_id=dataset_id, n_facts=len(facts))
    except Exception as exc:
        logger.warning("compress.dataset.error", dataset_id=dataset_id, error=str(exc))


def compress_memory() -> None:
    """Background task: extract facts from global_memory → global_memory_facts."""
    try:
        from data_analyst.db.session import create_db_session
        from data_analyst.db.models import SettingsRow
        with create_db_session() as db:
            row = db.get(SettingsRow, "global_memory")
            if row is None or not row.value:
                # If memory is cleared, clear the facts too
                facts_row = db.get(SettingsRow, "global_memory_facts")
                if facts_row:
                    facts_row.value = None
                return
            facts = extract_facts(row.value)
            if not facts:
                return
            facts_row = db.get(SettingsRow, "global_memory_facts")
            if facts_row is None:
                facts_row = SettingsRow(key="global_memory_facts", value=json.dumps(facts))
                db.add(facts_row)
            else:
                facts_row.value = json.dumps(facts)
            logger.info("compress.memory.ok", n_facts=len(facts))
    except Exception as exc:
        logger.warning("compress.memory.error", error=str(exc))
