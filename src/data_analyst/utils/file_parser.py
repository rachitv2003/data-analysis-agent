import hashlib
import io
import json as _json
from pathlib import Path

import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".txt", ".json", ".xlsx", ".xls"}


def compute_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def detect_format(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{ext}'. Accepted: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    # Normalise Excel variants to a single format token
    if ext in {".xlsx", ".xls"}:
        return "excel"
    return ext.lstrip(".")


def parse_file(raw: bytes, fmt: str) -> pd.DataFrame:
    if fmt == "excel":
        try:
            return pd.read_excel(io.BytesIO(raw), sheet_name=0)
        except Exception as exc:
            raise ValueError(f"Could not parse Excel file: {exc}") from exc
    if fmt == "csv":
        try:
            return pd.read_csv(io.BytesIO(raw), encoding="utf-8", encoding_errors="replace")
        except Exception as exc:
            raise ValueError(f"Could not parse CSV file: {exc}") from exc
    if fmt == "tsv":
        try:
            return pd.read_csv(io.BytesIO(raw), sep="\t", encoding="utf-8", encoding_errors="replace")
        except Exception as exc:
            raise ValueError(f"Could not parse TSV file: {exc}") from exc
    if fmt == "txt":
        try:
            return pd.read_csv(
                io.BytesIO(raw), sep=None, engine="python",
                encoding="utf-8", encoding_errors="replace",
            )
        except Exception as exc:
            raise ValueError(
                f"Could not parse .txt file as tabular data: {exc}. "
                "Make sure the file contains comma- or tab-separated values with a header row. "
                "If this is a free-form notes/context file, attach it using the '📎 Notes file' "
                "button in the staged list instead of uploading it as a dataset."
            ) from exc
    if fmt == "json":
        return _parse_json(raw)
    raise ValueError(f"Unknown format: {fmt}")


def _parse_json(raw: bytes) -> pd.DataFrame:
    text = raw.decode("utf-8", errors="replace")
    try:
        data = _json.loads(text)
    except _json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    # Strategy 1: array of objects
    if isinstance(data, list):
        try:
            return pd.DataFrame(data)
        except Exception as exc:
            raise ValueError(f"Could not parse JSON array: {exc}") from exc

    # Strategy 2: object whose first list value is the records
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list):
                try:
                    return pd.DataFrame(val)
                except Exception:
                    continue
        # Strategy 3: column-keyed object {"col": [v1, v2, ...]}
        try:
            return pd.DataFrame(data)
        except Exception as exc:
            raise ValueError(
                "unsupported_json_shape: Could not parse JSON object. "
                "Expected an array of records, or a dict with a list value, "
                f"or column-keyed object. Detail: {exc}"
            ) from exc

    raise ValueError("unsupported_json_shape: JSON root must be an array or object.")
