"""
The one way Rocky's modules call Claude.

Opus 5.5 by default (10/30), replacing Sonnet 4.5. Opus 5.5 always thinks
before it answers, so a reply's first block is usually a thinking block, not
the text (read replies with text(), never content[0].text), and the thinking
counts against max_tokens, so create() adds THINKING_ROOM on top of the reply
cap each caller asks for. It takes an effort level instead of temperature, and
it can decline: fallbacks "default" re-runs a declined request on a model the
API picks for the refusal category, and text() raises if the whole chain
declined.
"""

from __future__ import annotations

CLAUDE_MODEL = "claude-opus-5-5"

# Room for thinking on top of each caller's reply cap. Kept so the largest
# caller (8192) stays under the SDK's non-streaming limit.
THINKING_ROOM = 8000


def create(client, *, max_tokens: int, effort: str = "medium", **kw):
    """One Claude call; returns the response. kw is system/messages."""
    return client.beta.messages.create(
        model=CLAUDE_MODEL, max_tokens=max_tokens + THINKING_ROOM,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": effort}, **kw)


def text(response) -> str:
    """The reply's text blocks, joined. Raises if the model declined."""
    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("the model declined the request")
    return "".join(b.text for b in response.content
                   if getattr(b, "type", "text") == "text")
