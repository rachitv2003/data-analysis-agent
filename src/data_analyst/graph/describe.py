"""C30: Auto-generated dataset notes — background LLM call post-upload."""
import structlog

logger = structlog.get_logger()

_NOTES_PROMPT = (
    "You are a data analyst documenting a dataset. Given the structure and sample data below,\n"
    "write concise notes (max 300 words) in plain text describing:\n\n"
    "1. What this dataset appears to represent (one sentence overview).\n"
    "2. For each column: what it contains, its inferred meaning, example or typical values.\n"
    "3. Any data quality observations (null counts, suspicious values, date ranges, unit hints).\n\n"
    "Dataset filename: {filename}\n"
    "Columns:\n{columns_schema}\n\n"
    "Sample rows (up to 50):\n{sample_rows}\n\n"
    "Respond with plain text only. Be concise and factual."
)


def _mark_status(dataset_id: str, status: str) -> None:
    try:
        from data_analyst.db.session import create_db_session
        from data_analyst.db.models import DatasetRow
        with create_db_session() as db:
            row = db.get(DatasetRow, dataset_id)
            if row:
                row.auto_notes_status = status
    except Exception:
        pass


def generate_dataset_notes(dataset_id: str, overwrite: bool = False) -> None:
    """Background task: generate notes for a dataset via LLM and store in DatasetRow.context."""
    from pathlib import Path

    # Step 1: Read dataset info and decide whether to generate
    filename = file_path = parquet_path = None
    skip_generate = False

    try:
        from data_analyst.db.session import create_db_session
        from data_analyst.db.models import DatasetRow
        with create_db_session() as db:
            row = db.get(DatasetRow, dataset_id)
            if row is None:
                return
            filename = row.filename
            file_path = row.file_path
            parquet_path = row.parquet_path
            if row.context and not overwrite:
                skip_generate = True
    except Exception as exc:
        logger.error("describe.db_read_error", dataset_id=dataset_id, error=str(exc))
        return

    if skip_generate:
        # Notes already exist — just run C31 compression on them and mark done
        _mark_status(dataset_id, "done")
        try:
            from data_analyst.graph.compress import compress_dataset_context
            compress_dataset_context(dataset_id)
        except Exception:
            pass
        return

    # Step 2: Load sample data
    columns_schema = sample_str = None
    try:
        import pandas as pd
        if parquet_path and Path(parquet_path).exists():
            df = pd.read_parquet(parquet_path, engine="pyarrow")
        elif file_path and Path(file_path).exists():
            df = pd.read_csv(file_path, nrows=50)
        else:
            logger.warning("describe.no_file", dataset_id=dataset_id)
            _mark_status(dataset_id, "failed")
            return

        df_sample = df.head(50)
        columns_schema = "\n".join(
            f"- {col} ({str(dtype)})" for col, dtype in df_sample.dtypes.items()
        )
        sample_str = df_sample.to_json(orient="records")
        if len(sample_str) > 8000:
            sample_str = sample_str[:8000] + "... [truncated]"
    except Exception as exc:
        logger.warning("describe.load_error", dataset_id=dataset_id, error=str(exc))
        _mark_status(dataset_id, "failed")
        return

    # Step 3: Call LLM
    notes = None
    try:
        from data_analyst.graph.nodes import _get_llm
        prompt = _NOTES_PROMPT.format(
            filename=filename,
            columns_schema=columns_schema,
            sample_rows=sample_str,
        )
        llm = _get_llm()
        resp = llm.complete(prompt)
        notes = resp.text.strip()
    except Exception as exc:
        logger.error("describe.llm_error", dataset_id=dataset_id, error=str(exc))
        _mark_status(dataset_id, "failed")
        return

    if not notes:
        _mark_status(dataset_id, "failed")
        return

    # Step 4: Save notes and mark done
    try:
        from data_analyst.db.session import create_db_session
        from data_analyst.db.models import DatasetRow
        with create_db_session() as db:
            row = db.get(DatasetRow, dataset_id)
            if row:
                row.context = notes
                row.auto_notes_status = "done"
        logger.info("describe.ok", dataset_id=dataset_id, chars=len(notes))
    except Exception as exc:
        logger.error("describe.save_error", dataset_id=dataset_id, error=str(exc))
        return

    # Step 5: Trigger C31 compression on the new notes
    try:
        from data_analyst.graph.compress import compress_dataset_context
        compress_dataset_context(dataset_id)
    except Exception:
        pass
