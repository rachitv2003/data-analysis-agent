from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from data_analyst.db.session import init_db
    from data_analyst.graph.nodes import _get_llm  # warms up provider detection
    Path("uploads").mkdir(exist_ok=True)
    init_db()
    _get_llm()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Data Analysis Agent", version="0.1.0", lifespan=_lifespan)

    from data_analyst.api import health, upload, ask, datasets, sessions, dataset_sessions, stats, runs, ui
    app.include_router(health.router)
    app.include_router(upload.router)
    app.include_router(ask.router)
    app.include_router(datasets.router)
    app.include_router(sessions.router)
    app.include_router(dataset_sessions.router)
    app.include_router(stats.router)
    app.include_router(runs.router)
    app.include_router(ui.router)

    return app


app = create_app()
