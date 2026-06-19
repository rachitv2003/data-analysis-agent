from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from data_analyst.api._common import ok
from data_analyst.db.session import get_session
from data_analyst.db.models import QueryRunRow

router = APIRouter()


@router.get("/runs/current")
def get_current_run(session: Session = Depends(get_session)):
    """C22: Return the most-recently-created QueryRun for progress polling."""
    from data_analyst.config.settings import get_settings
    run = (
        session.query(QueryRunRow)
        .order_by(QueryRunRow.created_at.desc())
        .first()
    )
    max_iterations = get_settings().max_iterations
    if run is None:
        return ok({"run_id": None, "status": "idle", "iteration_count": 0, "max_iterations": max_iterations})
    return ok({
        "run_id": run.id,
        "status": run.status,
        "iteration_count": run.iteration_count,
        "max_iterations": max_iterations,
    })
