"""Small typed records used by the local context preparation pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContextBlock:
    """An indivisible extractive unit with stable source provenance."""

    block_id: str
    source_id: str
    unit_id: str
    locator: Any
    heading_path: tuple[str, ...]
    text: str
    kind: str
    order: int
    footnote_refs: tuple[str, ...] = field(default_factory=tuple)
    footnote_id: str | None = None
    heading_levels: tuple[int, ...] = field(default_factory=tuple)

    @property
    def section(self) -> str:
        return " > ".join(self.heading_path) if self.heading_path else "Unsectioned"


@dataclass(frozen=True)
class ContextOptions:
    max_tokens: int = 8000
    tokenizer: str = "cl100k_base"
    mode: str = "faithful"
    query: str = ""
    context_window: int | None = None
    reserved_tokens: int = 0
    answer_tokens: int = 0
    compression: str = "none"
    compression_rate: float = 0.5
    compressor_model: str | None = None


@dataclass(frozen=True)
class TokenBudget:
    budget_tokens: int
    tokenizer: str
    count_kind: str
