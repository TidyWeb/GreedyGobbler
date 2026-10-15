"""Deterministic lexical retrieval for focused context selection."""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable

from .context_models import ContextBlock

_TOKEN_RE = re.compile(r"[^\W_]+(?:[.-][^\W_]+)*", re.UNICODE)
_DEFINITION_RE = re.compile(
    r"\b(?:means|defined\s+as|refers\s+to|is\s+defined\s+as|denotes)\b",
    re.IGNORECASE,
)
_QUALIFICATION_RE = re.compile(
    r"\b(?:not|never|no|except|unless|only|must|may|cannot|can't|without|"
    r"at\s+least|at\s+most|however|although|but|subject\s+to)\b",
    re.IGNORECASE,
)


def lexical_tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]


def score_blocks(blocks: Iterable[ContextBlock], query: str) -> dict[str, float]:
    """Score blocks with BM25, heading matches, and nearby-context support."""
    materialized = list(blocks)
    query_terms = lexical_tokens(query)
    if not query_terms:
        return {block.block_id: 0.0 for block in materialized}

    documents = [
        lexical_tokens(block.text + " " + " ".join(block.heading_path))
        for block in materialized
    ]
    frequencies = [Counter(document) for document in documents]
    count = len(materialized)
    average_length = sum(map(len, documents)) / count if count else 0.0
    document_frequency: Counter[str] = Counter()
    for terms in documents:
        document_frequency.update(set(terms))

    k1 = 1.5
    b = 0.75
    scores: list[float] = []
    for block, terms, frequencies_for_block in zip(materialized, documents, frequencies):
        length = len(terms)
        score = 0.0
        for term in query_terms:
            frequency = frequencies_for_block.get(term, 0)
            if not frequency:
                continue
            inverse = math.log(
                1.0
                + (count - document_frequency[term] + 0.5)
                / (document_frequency[term] + 0.5)
            )
            denominator = frequency + k1 * (
                1.0 - b + b * length / (average_length or 1.0)
            )
            score += inverse * frequency * (k1 + 1.0) / denominator

        heading_terms = lexical_tokens(" ".join(block.heading_path))
        heading_frequency = Counter(heading_terms)
        for term in query_terms:
            if heading_frequency[term]:
                score += 0.75 * math.log(
                    1.0
                    + (count - document_frequency.get(term, 0) + 0.5)
                    / (document_frequency.get(term, 0) + 0.5)
                )

        if score and _DEFINITION_RE.search(block.text):
            score *= 1.2
        scores.append(score)

    # Neighboring blocks often carry a unit, negation, exception, or definition
    # needed to interpret the matching fact. Give them support before ranking.
    expanded = list(scores)
    for index, block in enumerate(materialized):
        for neighbor_index in (index - 1, index + 1):
            if neighbor_index < 0 or neighbor_index >= count:
                continue
            neighbor = materialized[neighbor_index]
            if (
                neighbor.source_id != block.source_id
                or neighbor.unit_id != block.unit_id
                or neighbor.heading_path != block.heading_path
            ):
                continue
            neighbor_text = neighbor.text
            if scores[neighbor_index] > 0:
                expanded[index] += scores[neighbor_index] * 0.2
            if scores[index] > 0 and _QUALIFICATION_RE.search(neighbor_text):
                expanded[neighbor_index] += scores[index] * 0.35

    return {
        block.block_id: expanded[index]
        for index, block in enumerate(materialized)
    }


def ranked_blocks(blocks: Iterable[ContextBlock], query: str) -> list[ContextBlock]:
    materialized = list(blocks)
    scores = score_blocks(materialized, query)
    return sorted(
        (block for block in materialized if scores[block.block_id] > 0),
        key=lambda block: (-scores[block.block_id], block.order),
    )
