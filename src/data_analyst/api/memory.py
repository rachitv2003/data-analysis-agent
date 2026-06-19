from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from data_analyst.api._common import ok
from data_analyst.db.session import get_session
from data_analyst.db.models import SettingsRow

router = APIRouter()

_MEMORY_KEY = "global_memory"


class MemoryUpdate(BaseModel):
    content: str


@router.get("/memory")
def get_memory(session: Session = Depends(get_session)):
    row = session.get(SettingsRow, _MEMORY_KEY)
    return ok({"content": row.value if row else ""})


@router.patch("/memory")
def update_memory(body: MemoryUpdate, session: Session = Depends(get_session)):
    row = session.get(SettingsRow, _MEMORY_KEY)
    if row is None:
        row = SettingsRow(key=_MEMORY_KEY, value=body.content)
        session.add(row)
    else:
        row.value = body.content
    return ok({"content": row.value})
