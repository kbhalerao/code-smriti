"""Shared optional fields added to LM Studio chat completion requests.

Driven by env config so we can swap LLM behavior without code edits.
Currently honors:
- LLM_REASONING_EFFORT: passed as `reasoning_effort` if set
  ("none" / "low" / "medium" / "high"). For thinking-capable models
  that obey the OpenAI-style reasoning_effort field (e.g. Gemma 4).
  Models without a reasoning channel (e.g. Qwen3-30B-A3B-2507 Instruct)
  ignore the field, so it's safe to always send.
"""

import os
from typing import Any


def llm_request_extras() -> dict[str, Any]:
    """Return a dict of optional LM Studio request fields from env config."""
    extras: dict[str, Any] = {}
    effort = os.getenv("LLM_REASONING_EFFORT")
    if effort:
        extras["reasoning_effort"] = effort
    return extras
