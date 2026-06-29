from fastapi import APIRouter

from api._common import ok

router = APIRouter()


def _provider_and_model() -> tuple[str, str]:
    """Resolve the active LLM provider + model for the UI.

    Wrapped so a provider-init issue never 500s `/health` — any exception falls
    back to `stub` (the offline default) with an empty model.
    """
    try:
        from llm.client import LLMClient
        client = LLMClient()
        return client.provider, client.model
    except Exception:
        return "stub", ""


@router.get("/health")
def health() -> dict:
    provider, model = _provider_and_model()
    return ok({"status": "ok", "provider": provider, "model": model})
