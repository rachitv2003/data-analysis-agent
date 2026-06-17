import uvicorn

from data_analyst.api import app  # noqa: F401 — side effect: registers routes

if __name__ == "__main__":
    uvicorn.run(
        "data_analyst.api:app",
        host="0.0.0.0",
        port=8001,
        reload=False,
    )
