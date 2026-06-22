import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow, ConversationSessionRow, QueryRunRow

router = APIRouter()

_CONTEXT_MAX_LEN = 4000


def _friendly_dtype(dtype_str: str) -> str:
    s = dtype_str.lower()
    if s in ("object", "string", "str", "large_string"): return "text"
    if s.startswith("int"): return "integer"
    if s.startswith("uint"): return "integer"
    if s.startswith("float"): return "float"
    if s.startswith("datetime"): return "datetime"
    if s.startswith("timedelta"): return "duration"
    if s in ("bool", "boolean"): return "boolean"
    if s.startswith("category"): return "category"
    return dtype_str


def _stale(row: DatasetRow, db: Session) -> bool:
    """True if any parent dataset was updated after this derived dataset was created."""
    if row.origin != "derived" or not row.derived_from_dataset_ids:
        return False
    try:
        parent_ids = json.loads(row.derived_from_dataset_ids)
    except Exception:
        return False
    for pid in parent_ids:
        parent = db.get(DatasetRow, pid)
        if parent is None:
            return True  # parent deleted → treat as stale
        parent_updated = getattr(parent, "updated_at", None) or parent.created_at
        if parent_updated > row.created_at:
            return True
    return False


def _row_dict(row: DatasetRow, db: Session) -> dict:
    return {
        "dataset_id": row.id,
        "filename": row.filename,
        "format": row.format,
        "context": row.context or "",
        "row_count": row.row_count,
        "col_count": row.col_count,
        "columns": json.loads(row.columns_json),
        "created_at": row.created_at.isoformat(),
        "origin": row.origin or "uploaded",
        "stale": _stale(row, db),
        "derived_from_run_id": row.derived_from_run_id,
        "derived_from_dataset_ids": (
            json.loads(row.derived_from_dataset_ids)
            if row.derived_from_dataset_ids else None
        ),
        "derivation_description": row.context if row.origin == "derived" else None,
        "auto_notes_status": row.auto_notes_status,
    }


@router.get("/datasets")
def list_datasets(session: Session = Depends(get_session)):
    rows = session.query(DatasetRow).order_by(DatasetRow.created_at.desc()).all()
    return ok([_row_dict(r, session) for r in rows])


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, session: Session = Depends(get_session)):
    row = session.get(DatasetRow, dataset_id)
    if row is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)

    # Build columns_schema from Parquet dtypes (fast) or CSV header (fallback)
    columns_schema: list[dict] = []
    try:
        if row.parquet_path and Path(row.parquet_path).exists():
            import pandas as pd
            df0 = pd.read_parquet(row.parquet_path, engine="pyarrow")
            columns_schema = [
                {"name": col, "dtype": _friendly_dtype(str(dtype))}
                for col, dtype in df0.dtypes.items()
            ]
        elif row.file_path and Path(row.file_path).exists():
            import pandas as pd
            # nrows=200 gives pandas enough data to infer numeric/date types
            df0 = pd.read_csv(row.file_path, nrows=200)
            columns_schema = [
                {"name": col, "dtype": _friendly_dtype(str(dtype))}
                for col, dtype in df0.dtypes.items()
            ]
    except Exception:
        # Fall back to columns_json without dtype info
        columns_schema = [
            {"name": col, "dtype": "unknown"}
            for col in json.loads(row.columns_json)
        ]

    data = _row_dict(row, session)
    data["columns_schema"] = columns_schema
    data["derivation_code"] = row.derivation_code
    return ok(data)


class ContextUpdate(BaseModel):
    context: str


@router.patch("/datasets/{dataset_id}/context")
def update_context(
    dataset_id: str,
    body: ContextUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    if len(body.context) > _CONTEXT_MAX_LEN:
        raise api_error("context_too_long", f"Context must be ≤ {_CONTEXT_MAX_LEN} characters.")

    row = session.get(DatasetRow, dataset_id)
    if row is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)

    row.context = body.context.strip() or None
    if not row.context:
        row.context_facts = None
    # Commit before queuing background task so the task sees the new context
    session.commit()
    # C31: compress updated notes into facts in background
    if row.context:
        from data_analyst.graph.compress import compress_dataset_context
        background_tasks.add_task(compress_dataset_context, dataset_id)

    return ok({"dataset_id": dataset_id, "context": row.context or ""})


@router.post("/datasets/{dataset_id}/describe")
def describe_dataset(
    dataset_id: str,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    """C30: Trigger (re-)generation of auto-notes for a dataset."""
    row = session.get(DatasetRow, dataset_id)
    if row is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)

    row.auto_notes_status = "pending"
    # Commit immediately so the generator cleanup doesn't overwrite the background task's "done"
    session.commit()
    from data_analyst.graph.describe import generate_dataset_notes
    background_tasks.add_task(generate_dataset_notes, dataset_id, True)
    return ok({"dataset_id": dataset_id, "auto_notes_status": "pending"})


@router.post("/datasets/{dataset_id}/re-derive")
def re_derive_dataset(dataset_id: str, session: Session = Depends(get_session)):
    """C25: Re-execute the derivation code against current parent versions."""
    row = session.get(DatasetRow, dataset_id)
    if row is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)
    if row.origin != "derived":
        raise api_error("not_derived", "Only derived datasets can be re-derived.", 400)
    if not row.derivation_code:
        raise api_error("no_derivation_code", "Dataset has no stored derivation code.", 400)

    parent_ids: list[str] = []
    if row.derived_from_dataset_ids:
        try:
            parent_ids = json.loads(row.derived_from_dataset_ids)
        except Exception:
            raise api_error("re_derive_error", "Could not parse parent dataset IDs.", 400)

    import pandas as pd
    from data_analyst.graph.nodes import _var_name

    ns: dict = {"pd": pd}
    for pid in parent_ids:
        parent = session.get(DatasetRow, pid)
        if parent is None:
            raise api_error("parent_not_found", f"Parent dataset {pid} not found.", 404)
        try:
            if parent.parquet_path and Path(parent.parquet_path).exists():
                df = pd.read_parquet(parent.parquet_path, engine="pyarrow")
            else:
                df = pd.read_csv(parent.file_path)
            var = _var_name(parent.filename)
            ns[var] = df
            ns["df"] = df
        except Exception as exc:
            raise api_error("re_derive_error", f"Could not load parent {pid}: {exc}", 400)

    try:
        lines = row.derivation_code.strip().splitlines()
        preamble = "\n".join(lines[:-1])
        last = lines[-1].strip()
        if preamble:
            exec(preamble, ns)  # noqa: S102
        try:
            result = eval(last, ns)  # noqa: S307
        except SyntaxError:
            exec(last, ns)  # noqa: S102
            result = None

        if result is None or not isinstance(result, pd.DataFrame):
            raise ValueError("Derivation code did not return a DataFrame as its last expression")
        cleaned = result
    except Exception as exc:
        raise api_error("re_derive_error", f"Derivation code failed: {exc}", 400)

    # Overwrite CSV + Parquet
    csv_path = Path(row.file_path)
    cleaned.to_csv(csv_path, index=False)
    if row.parquet_path:
        try:
            cleaned.to_parquet(row.parquet_path, engine="pyarrow", index=False)
        except Exception:
            pass

    # Update DB metadata and timestamp
    row.row_count = len(cleaned)
    row.col_count = len(cleaned.columns)
    row.columns_json = json.dumps(cleaned.columns.tolist())
    row.updated_at = datetime.now(timezone.utc)

    # Invalidate session cache
    try:
        from data_analyst.graph.nodes import _invalidate_dataset
        _invalidate_dataset(dataset_id)
    except Exception:
        pass

    # Refresh row from DB and return full GET /datasets/{id} shape
    session.refresh(row)
    columns_schema: list[dict] = []
    try:
        if row.parquet_path and Path(row.parquet_path).exists():
            cols_schema_df = cleaned
        else:
            cols_schema_df = cleaned
        columns_schema = [{"name": col, "dtype": _friendly_dtype(str(dtype))} for col, dtype in cleaned.dtypes.items()]
    except Exception:
        columns_schema = [{"name": col, "dtype": "unknown"} for col in json.loads(row.columns_json)]

    data = _row_dict(row, session)
    data["columns_schema"] = columns_schema
    data["derivation_code"] = row.derivation_code
    return ok(data)


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, session: Session = Depends(get_session)):
    row = session.get(DatasetRow, dataset_id)
    if row is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)

    running = None
    for _run in session.query(QueryRunRow).filter(QueryRunRow.status == "running").all():
        if _run.dataset_id == dataset_id:
            running = _run
            break
        if _run.dataset_ids_json:
            try:
                if dataset_id in json.loads(_run.dataset_ids_json):
                    running = _run
                    break
            except Exception:
                pass
    if running:
        raise api_error("dataset_in_use", "A query is currently running against this dataset.", 409)

    result = _cascade_delete(session, [dataset_id])
    return ok(result)


@router.delete("/datasets")
def delete_all_datasets(session: Session = Depends(get_session)):
    rows = session.query(DatasetRow).all()
    if not rows:
        return ok({"deleted_dataset_ids": [], "deleted_session_count": 0, "deleted_run_count": 0, "derived_deleted": 0})

    running = session.query(QueryRunRow).filter(QueryRunRow.status == "running").first()
    if running:
        raise api_error("dataset_in_use", "A query is currently running. Cannot delete datasets.", 409)

    result = _cascade_delete(session, [r.id for r in rows])
    return ok(result)


def _cascade_delete(db: Session, dataset_ids: list[str]) -> dict:
    deleted_run_count = 0
    deleted_session_count = 0
    derived_deleted = 0
    id_set = set(dataset_ids)

    # C25: recursively collect derived datasets that depend on any deleted dataset
    def _collect_derived(ids: set[str]) -> set[str]:
        collected: set[str] = set()
        to_expand = set(ids)
        while to_expand:
            all_derived = db.query(DatasetRow).filter(DatasetRow.origin == "derived").all()
            next_expand: set[str] = set()
            for d in all_derived:
                if d.id in collected:
                    continue
                if not d.derived_from_dataset_ids:
                    continue
                try:
                    parents = set(json.loads(d.derived_from_dataset_ids))
                except Exception:
                    continue
                if parents & to_expand:
                    collected.add(d.id)
                    next_expand.add(d.id)
            to_expand = next_expand
        return collected

    extra = _collect_derived(id_set)
    all_ids = id_set | extra
    derived_deleted = len(extra)

    # Find sessions that reference any of these datasets
    all_sessions = db.query(ConversationSessionRow).all()
    sessions_to_delete: set[str] = set()
    for sess in all_sessions:
        sess_ids = set(json.loads(sess.dataset_ids_json)) if sess.dataset_ids_json else {sess.dataset_id}
        if sess_ids & all_ids:
            sessions_to_delete.add(sess.id)

    # Delete runs in those sessions + orphan runs directly linked to the datasets
    for sess_id in sessions_to_delete:
        for run in db.query(QueryRunRow).filter(QueryRunRow.session_id == sess_id).all():
            db.delete(run)
            deleted_run_count += 1
        sess_row = db.get(ConversationSessionRow, sess_id)
        if sess_row:
            db.delete(sess_row)
            deleted_session_count += 1

    for did in all_ids:
        for run in db.query(QueryRunRow).filter(
            QueryRunRow.dataset_id == did,
            QueryRunRow.session_id.is_(None),
        ).all():
            db.delete(run)
            deleted_run_count += 1

    # Delete dataset rows + CSV + Parquet files
    for did in all_ids:
        row = db.get(DatasetRow, did)
        if row:
            try:
                Path(row.file_path).unlink(missing_ok=True)
            except Exception:
                pass
            if row.parquet_path:
                try:
                    Path(row.parquet_path).unlink(missing_ok=True)
                except Exception:
                    pass
            db.delete(row)

    # Evict from session cache
    try:
        from data_analyst.graph.nodes import _invalidate_dataset
        for did in all_ids:
            _invalidate_dataset(did)
    except Exception:
        pass

    return {
        "deleted_dataset_ids": list(dataset_ids),
        "deleted_session_count": deleted_session_count,
        "deleted_run_count": deleted_run_count,
        "derived_deleted": derived_deleted,
    }
