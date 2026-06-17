from fastapi import APIRouter
from data_analyst.api._common import ok

router = APIRouter()


@router.get("/health")
def health_check():
    return ok({"status": "ok"})
