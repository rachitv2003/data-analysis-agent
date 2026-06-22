from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from data_analyst.api._common import ok
from data_analyst.db.session import get_session
from data_analyst.db.models import SettingsRow

router = APIRouter()

_MEMORY_KEY = "global_memory"
_MEMORY_FACTS_KEY = "global_memory_facts"


class MemoryUpdate(BaseModel):
    content: str


@router.get("/memory")
def get_memory(session: Session = Depends(get_session)):
    row = session.get(SettingsRow, _MEMORY_KEY)
    return ok({"content": row.value if row else ""})


@router.patch("/memory")
def update_memory(
    body: MemoryUpdate,
    background_tasks: BackgroundTasks,
    session: Session = Depends(get_session),
):
    row = session.get(SettingsRow, _MEMORY_KEY)
    if row is None:
        row = SettingsRow(key=_MEMORY_KEY, value=body.content)
        session.add(row)
    else:
        row.value = body.content

    # C31: clear stale facts immediately — they describe the OLD memory text. Until
    # recompression completes, the prompt builder falls back to the fresh raw memory
    # rather than serving facts that no longer match.
    facts_row = session.get(SettingsRow, _MEMORY_FACTS_KEY)
    if facts_row:
        facts_row.value = None

    # Commit before queuing background task so compress_memory sees the new value
    session.commit()

    # C31: compress memory facts in background
    from data_analyst.graph.compress import compress_memory
    background_tasks.add_task(compress_memory)

    return ok({"content": row.value})
