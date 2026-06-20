import io
import json as _json
import re

import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from data_analyst.api._common import ok, api_error
from data_analyst.db.session import get_session
from data_analyst.db.models import DatasetRow

router = APIRouter()

_MAX_PREVIEW = 5


class CleanRequest(BaseModel):
    instruction: str


class ApplyRequest(BaseModel):
    code: str


def _gen_clean_code(df: pd.DataFrame, var: str, instruction: str) -> str:
    from data_analyst.graph.nodes import _get_llm
    import re as _re

    schema = f"{var}: {len(df)} rows × {len(df.columns)} cols — columns: {', '.join(df.columns.tolist()[:30])}"
    prompt = (
        "You are a data cleaning assistant. Given a pandas DataFrame, generate Python code to apply "
        "the requested cleaning operation. Return ONLY the Python code block (no markdown fences, no explanation).\n\n"
        f"DataFrame: {schema}\n"
        f"The DataFrame variable is named `{var}`.\n"
        f"Instruction: {instruction}\n\n"
        "Rules:\n"
        f"- Use only pandas operations on `{var}`\n"
        f"- The last expression must be the cleaned DataFrame (assign to `{var}` and return it, or just return it)\n"
        "- Do NOT write to files or import anything\n"
        f"- If the last line is an assignment like `{var} = ...`, add a final line `{var}` so the result is returned\n"
    )

    llm = _get_llm()
    resp = llm.complete(prompt)
    code = resp.text.strip()
    code = _re.sub(r"^```[a-zA-Z]*\n?", "", code)
    code = _re.sub(r"\n?```$", "", code).strip()
    return code


def _exec_clean(df: pd.DataFrame, var: str, code: str) -> pd.DataFrame:
    ns = {var: df.copy(), "pd": pd}
    lines = code.strip().splitlines()
    preamble = "\n".join(lines[:-1])
    last = lines[-1].strip() if lines else ""
    if preamble:
        exec(preamble, ns)  # noqa: S102
    try:
        result = eval(last, ns)  # noqa: S307
        if isinstance(result, pd.DataFrame):
            return result
    except SyntaxError:
        exec(last, ns)  # noqa: S102
    # If last line was assignment, the var should now be updated
    return ns.get(var, df)


@router.post("/datasets/{dataset_id}/clean")
def preview_clean(
    dataset_id: str,
    body: CleanRequest,
    session: Session = Depends(get_session),
):
    ds = session.get(DatasetRow, dataset_id)
    if ds is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)
    if not body.instruction.strip():
        raise api_error("empty_instruction", "Instruction cannot be empty.")

    var = re.sub(r"[^\w]", "_", ds.filename.rsplit(".", 1)[0].lower()).strip("_") or "df"

    try:
        df = pd.read_csv(ds.file_path)
    except Exception as exc:
        raise api_error("read_error", f"Could not read dataset: {exc}", 500)

    try:
        code = _gen_clean_code(df, var, body.instruction)
    except Exception as exc:
        raise api_error("llm_error", f"Could not generate cleaning code: {exc}", 500)

    try:
        cleaned = _exec_clean(df, var, code)
    except Exception as exc:
        raise api_error("exec_error", f"Generated code failed: {exc}. Code was:\n{code}", 422)

    before_preview = df.head(_MAX_PREVIEW).to_dict(orient="records")
    after_preview = cleaned.head(_MAX_PREVIEW).to_dict(orient="records")

    return ok({
        "code": code,
        "row_count_before": len(df),
        "col_count_before": len(df.columns),
        "row_count_after": len(cleaned),
        "col_count_after": len(cleaned.columns),
        "columns_after": cleaned.columns.tolist(),
        "preview_before": before_preview,
        "preview_after": after_preview,
    })


@router.post("/datasets/{dataset_id}/clean/apply")
def apply_clean(
    dataset_id: str,
    body: ApplyRequest,
    session: Session = Depends(get_session),
):
    ds = session.get(DatasetRow, dataset_id)
    if ds is None:
        raise api_error("dataset_not_found", f"Dataset {dataset_id} not found.", 404)
    if not body.code.strip():
        raise api_error("empty_code", "Code cannot be empty.")

    var = re.sub(r"[^\w]", "_", ds.filename.rsplit(".", 1)[0].lower()).strip("_") or "df"

    try:
        df = pd.read_csv(ds.file_path)
    except Exception as exc:
        raise api_error("read_error", f"Could not read dataset: {exc}", 500)

    try:
        cleaned = _exec_clean(df, var, body.code)
    except Exception as exc:
        raise api_error("exec_error", f"Code execution failed: {exc}", 422)

    try:
        cleaned.to_csv(ds.file_path, index=False)
    except Exception as exc:
        raise api_error("write_error", f"Could not save cleaned dataset: {exc}", 500)

    # Update DB metadata
    ds.row_count = len(cleaned)
    ds.col_count = len(cleaned.columns)
    ds.columns_json = _json.dumps(cleaned.columns.tolist())

    return ok({
        "row_count": len(cleaned),
        "col_count": len(cleaned.columns),
        "columns": cleaned.columns.tolist(),
    })
