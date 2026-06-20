import json
import re
import threading
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

_TIMEOUT_SECS = 60


@dataclass
class ClarifyResult:
    needs_clarification: bool
    question: str  # the clarification question to present to the user
    tokens_input: int = 0
    tokens_output: int = 0


def check_clarification(
    question: str,
    datasets: list[dict],
    history: list[dict],
) -> ClarifyResult:
    """C26 pre-flight clarification check.

    Enforces a 60-second wall-clock timeout and fails-open (returns
    needs_clarification=False) on timeout or any parse/LLM error.
    """
    result_holder: list[ClarifyResult] = []
    exception_holder: list[Exception] = []

    def _run() -> None:
        try:
            from data_analyst.graph.nodes import _get_llm

            schema_lines = []
            for ds in datasets:
                cols = ", ".join((ds.get("columns") or [])[:20])
                schema_lines.append(
                    f"- {ds['filename']} "
                    f"({ds.get('row_count', '?')} rows, {ds.get('col_count', '?')} cols): {cols}"
                )
            schema_text = "\n".join(schema_lines) or "No datasets"

            history_text = ""
            if history:
                conv_lines = [
                    f"Q: {t['question']}\nA: {(t.get('answer') or '')[:300]}"
                    for t in history[-5:]
                ]
                history_text = "\nPrior conversation:\n" + "\n\n".join(conv_lines)

            prompt = (
                "<node:clarify>\n"
                "You are a data analysis assistant. Given a user question, the available "
                "datasets, and any prior conversation, decide whether the question is "
                "ambiguous enough that a single targeted clarification would significantly "
                "improve the analysis.\n\n"
                "Clarification is NOT needed for:\n"
                "- Straightforward analytical questions ('what is the average?', 'top 10', etc.)\n"
                "- Questions the analyst can reasonably interpret and answer\n"
                "- Schema-level questions (describe data, list columns, etc.)\n\n"
                "Clarification IS needed when:\n"
                "- There are multiple plausible interpretations leading to very different analyses\n"
                "- A key filter or grouping is not specified and cannot be inferred from context\n"
                "- The question refers to something absent from the schema with no clear inference\n\n"
                "Default to NOT needing clarification — fail-open is always preferred.\n\n"
                f"Available datasets:\n{schema_text}"
                f"{history_text}\n\n"
                f"User question: {question}\n\n"
                "Respond with valid JSON only (no markdown fences):\n"
                '{"needs_clarification": false} or '
                '{"needs_clarification": true, "question": "<one focused question>"}\n'
                "</node:clarify>"
            )

            llm = _get_llm()
            resp = llm.complete(prompt)
            ti = getattr(resp, "tokens_input", 0) or 0
            to = getattr(resp, "tokens_output", 0) or 0
            text = resp.text.strip()
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
            parsed = json.loads(text)

            if parsed.get("needs_clarification") and parsed.get("question"):
                result_holder.append(
                    ClarifyResult(needs_clarification=True, question=parsed["question"], tokens_input=ti, tokens_output=to)
                )
            else:
                result_holder.append(ClarifyResult(needs_clarification=False, question="", tokens_input=ti, tokens_output=to))
        except Exception as exc:
            exception_holder.append(exc)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=_TIMEOUT_SECS)

    if result_holder:
        r = result_holder[0]
        if r.needs_clarification:
            logger.info("clarify.needed", preview=r.question[:80])
        return r

    if exception_holder:
        logger.warning("clarify.error", error=str(exception_holder[0]))
    else:
        logger.warning("clarify.timeout", question_preview=question[:80])

    return ClarifyResult(needs_clarification=False, question="")
