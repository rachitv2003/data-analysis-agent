from datetime import datetime
from pydantic import BaseModel


class Dataset(BaseModel):
    id: str
    filename: str
    file_path: str
    row_count: int
    col_count: int
    columns: list[str]
    created_at: datetime


class QueryRun(BaseModel):
    id: str
    dataset_id: str
    question: str
    answer: str | None
    status: str
    error_message: str | None
    action_history: list[dict]
    iteration_count: int
    created_at: datetime
    updated_at: datetime
