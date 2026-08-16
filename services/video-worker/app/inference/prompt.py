from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PROMPT_VERSION = "behavior-observer-v1"
SCHEMA_VERSION = "behavior-analysis-v1"


@lru_cache(maxsize=1)
def load_prompt() -> str:
    prompt_path = Path(__file__).resolve().parents[2] / "prompts" / f"{PROMPT_VERSION}.md"
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if PROMPT_VERSION not in prompt or SCHEMA_VERSION not in prompt:
        raise RuntimeError("versioned prompt does not declare its prompt and schema versions")
    return prompt
