import json
from pathlib import Path

from fastapi import APIRouter, UploadFile, File, Depends, Query
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow
from data_analyst.utils.file_parser import compute_hash, detect_format, parse_file

router = APIRouter()


@router.post("/upload")
def upload_file(
    file: UploadFile = File(...),
    force: bool = Query(default=False),
    session: Session = Depends(get_session),
):
    if not file.filename:
        raise api_error("invalid_file", "No filename provided.")

    # Detect format — raises ValueError for unsupported extensions
    try:
        fmt = detect_format(file.filename)
    except ValueError as exc:
        raise api_error("unsupported_format", str(exc))

    raw = file.file.read()
    content_hash = compute_hash(raw)

    # ── Duplicate detection (skip if force=True) ──────────────────────────────
    if not force:
        by_hash = (
            session.query(DatasetRow)
            .filter(DatasetRow.content_hash == content_hash, DatasetRow.content_hash != "")
            .first()
        )
        by_name = (
            session.query(DatasetRow)
            .filter(DatasetRow.filename == file.filename)
            .first()
        )

        if by_hash or by_name:
            match_row = by_hash or by_name
            if by_hash and by_name and by_hash.id == by_name.id:
                match_type = "both"
            elif by_hash:
                match_type = "content"
            else:
                match_type = "filename"

            from data_analyst.api._common import api_error as _err
            from fastapi import HTTPException
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "duplicate_dataset",
                    "message": (
                        f"A dataset with the same {'filename' if match_type == 'filename' else 'contents'} "
                        f"already exists: '{match_row.filename}'."
                    ),
                    "match_type": match_type,
                    "existing_dataset_id": match_row.id,
                    "existing_filename": match_row.filename,
                    "existing_created_at": match_row.created_at.isoformat(),
                },
            )

    # ── Parse ──────────────────────────────────────────────────────────────────
    try:
        df = parse_file(raw, fmt)
    except ValueError as exc:
        code = "unsupported_json_shape" if "unsupported_json_shape" in str(exc) else "parse_error"
        raise api_error(code, str(exc))

    if df.empty or len(df.columns) == 0:
        raise api_error("empty_file", "The uploaded file has no data rows.")

    # ── Persist ────────────────────────────────────────────────────────────────
    from data_analyst.config.settings import get_settings
    upload_dir = Path(get_settings().upload_dir)
    upload_dir.mkdir(exist_ok=True)

    dataset = DatasetRow(
        filename=file.filename,
        file_path="",
        row_count=len(df),
        col_count=len(df.columns),
        columns_json=json.dumps(df.columns.tolist()),
        content_hash=content_hash,
        format=fmt,
    )
    session.add(dataset)
    session.flush()

    # Save as CSV internally regardless of source format (uniform read path for agent)
    dest = upload_dir / f"{dataset.id}.csv"
    df.to_csv(dest, index=False)
    dataset.file_path = str(dest.resolve())

    return ok({
        "dataset_id": dataset.id,
        "filename": file.filename,
        "format": fmt,
        "row_count": len(df),
        "col_count": len(df.columns),
        "columns": df.columns.tolist(),
    })
