import structlog
from markdown_it import MarkdownIt

logger = structlog.get_logger()

_md = MarkdownIt("js-default", {"html": False}).enable("table")


def render_markdown(text: str) -> str:
    """Convert Markdown to sanitised HTML. Falls back to <pre> on error."""
    if not text:
        return ""
    try:
        return _md.render(text)
    except Exception as exc:
        logger.warning("markdown.render_error", error=str(exc))
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<pre>{escaped}</pre>"
