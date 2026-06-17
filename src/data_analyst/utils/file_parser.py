import hashlib
import io
import json as _json
from pathlib import Path

import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".txt", ".json"}


def compute_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def detect_format(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type '{ext}'. Accepted: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
    return ext.lstrip(".")


def parse_file(raw: bytes, fmt: str) -> pd.DataFrame:
    if fmt == "csv":
        return pd.read_csv(io.BytesIO(raw), encoding="utf-8", encoding_errors="replace")
    if fmt == "tsv":
        return pd.read_csv(io.BytesIO(raw), sep="\t", encoding="utf-8", encoding_errors="replace")
    if fmt == "txt":
        return pd.read_csv(
            io.BytesIO(raw), sep=None, engine="python",
            encoding="utf-8", encoding_errors="replace",
        )
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
