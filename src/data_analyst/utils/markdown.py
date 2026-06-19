import re
import structlog
from markdown_it import MarkdownIt

logger = structlog.get_logger()

_md = MarkdownIt("js-default", {"html": False}).enable("table")

# Matches server-generated chart divs appended by finalize().
# data-spec attribute value is HTML-escaped so it contains no literal quotes.
_CHART_DIV_RE = re.compile(r'<div class="plotly-chart" data-spec="[^"]*"></div>')


def render_markdown(text: str) -> str:
    """Convert Markdown to sanitised HTML. Falls back to <pre> on error."""
    if not text:
        return ""
    try:
        # Extract server-generated chart divs before rendering so that html:False
        # sanitises any HTML the LLM injects while still allowing chart divs through.
        chart_divs = _CHART_DIV_RE.findall(text)
        clean_text = _CHART_DIV_RE.sub("", text).strip()
        rendered = _md.render(clean_text)
        if chart_divs:
            rendered = rendered + "\n" + "\n".join(chart_divs)
        return rendered
    except Exception as exc:
        logger.warning("markdown.render_error", error=str(exc))
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<pre>{escaped}</pre>"
