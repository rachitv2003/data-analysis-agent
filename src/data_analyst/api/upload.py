import json
import shutil
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, UploadFile, File, Depends
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow

router = APIRouter()


@router.post("/upload")
def upload_csv(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise api_error("invalid_file", "Only .csv files are accepted.")

    from data_analyst.config.settings import get_settings
    upload_dir = Path(get_settings().upload_dir)
    upload_dir.mkdir(exist_ok=True)

    # Read into pandas first to validate + get shape
    try:
        df = pd.read_csv(file.file)
    except Exception as exc:
        raise api_error("parse_error", f"Could not parse CSV: {exc}")

    if df.empty:
        raise api_error("empty_file", "The uploaded CSV has no data rows.")

    # Save to disk
    dataset = DatasetRow(
        filename=file.filename,
        file_path="",  # filled after we know the id
        row_count=len(df),
        col_count=len(df.columns),
        columns_json=json.dumps(df.columns.tolist()),
    )
    session.add(dataset)
    session.flush()  # get the id

    dest = upload_dir / f"{dataset.id}.csv"
    df.to_csv(dest, index=False)
    dataset.file_path = str(dest.resolve())

    return ok({
        "dataset_id": dataset.id,
        "filename": file.filename,
        "row_count": len(df),
        "col_count": len(df.columns),
        "columns": df.columns.tolist(),
    })
