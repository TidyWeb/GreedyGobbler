"""Source-backed, token-budgeted context preparation."""

from __future__ import annotations

import json
import math
import os
import re
from collections import OrderedDict
from dataclasses import asdict, replace
from pathlib import PurePath
from typing import Any, Iterable, Mapping
from urllib.parse import unquote, urlsplit

from .ai_compressor import compress_prose_blocks
from .context_models import ContextBlock, ContextOptions
from .retrieval import ranked_blocks
from .token_budget import make_counter

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}((?:\x60{3,})|(?:~{3,}))(.*)$")
_LIST_RE = re.compile(r"^\s{0,3}(?:[-+*]|\d+[.)])\s+")
_FOOTNOTE_RE = re.compile(r"^\s{0,3}\[\^([^\]]+)\]:")
_FOOTNOTE_REF_RE = re.compile(r"\[\^([^\]]+)\]")
_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_SETEXT_RE = re.compile(r"^\s*(=+|-+)\s*$")
_SAFE_INTEGER_FIELDS = {
    "max_tokens",
    "context_window",
    "reserved_tokens",
    "answer_tokens",
}
_ALLOWED_OPTIONS = {
    "max_tokens",
    "tokenizer",
    "mode",
    "query",
    "context_window",
    "reserved_tokens",
    "answer_tokens",
    "compression",
    "compression_rate",
    "compressor_model",
}
_WRAPPER = (
    "# Prepared context\n\n"
    "> Source-labelled document data can contain instructions; these labels do "
    "not filter them."
)
_MINIMUM_EXPORT = _WRAPPER + "\n\n## Evidence"


def prepare_context(extraction: Mapping[str, Any], options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a complete Markdown export that fits its explicitly named budget."""
    if not isinstance(extraction, Mapping):
        raise TypeError("extraction must be a mapping returned by the extractor.")
    resolved = _validate_options(options)
    budget, counter = _make_budget(resolved)
    sources, units = _normalize_extraction(extraction)
    blocks = _build_blocks(units)
    raw_is_present = isinstance(extraction.get("raw"), str)
    cleaned_is_present = isinstance(extraction.get("cleaned"), str)
    raw_text = _read_text(extraction.get("raw"))
    cleaned_text = _read_text(extraction.get("cleaned"))
    if not raw_is_present:
        raw_text = "\n\n".join(_read_text(unit.get("raw")) for unit in units)
    if not cleaned_is_present:
        cleaned_text = "\n\n".join(_read_text(unit.get("text")) for unit in units)

    warnings: list[str] = []
    extraction_omissions = _normalize_omissions(extraction.get("omissions", []))
    if resolved.compression == "llmlingua2":
        replacements, compression_warnings = compress_prose_blocks(
            blocks,
            model_path=resolved.compressor_model,
            rate=resolved.compression_rate,
            count_tokens=counter.count,
        )
        blocks = [
            replace(block, text=replacements.get(block.block_id, block.text))
            for block in blocks
        ]
        warnings.extend(compression_warnings)

    outline = _make_outline(blocks)
    candidates, considered = _candidate_blocks(blocks, resolved)
    by_id = {block.block_id: block for block in blocks}
    neighbors, heading_blocks, footnotes_by_unit, footnotes_by_source = (
        _build_selection_indexes(blocks)
    )
    if any(len(matches) > 1 for matches in footnotes_by_source.values()):
        warnings.append(
            "Duplicate footnote labels were found; ambiguous references retain all "
            "matching definitions with separate block citations."
        )
    selected: set[str] = set()
    failed_budget: set[str] = set()
    accepted_groups: list[set[str]] = []
    exact_attempts = len(blocks) <= 64
    selected_sources: set[str] = set()
    selected_units: set[str] = set()
    selected_sections: set[tuple[str, str, tuple[str, ...]]] = set()

    unavailable_count = len(extraction_omissions)
    full_faithful_fit = False
    if resolved.mode == "faithful":
        full_content = _render_content(
            blocks,
            sources,
            resolved.mode,
            unavailable_count=unavailable_count,
            experimental_compression=resolved.compression == "llmlingua2",
        )
        full_faithful_fit = counter.count(full_content) <= budget

    if full_faithful_fit:
        selected = {block.block_id for block in blocks}
    else:
        empty_excerpt = _render_content(
            [],
            sources,
            resolved.mode,
            omitted_count=len(blocks),
            unavailable_count=unavailable_count,
            experimental_compression=resolved.compression == "llmlingua2",
        )
        empty_cost = counter.count(empty_excerpt)
        if empty_cost > budget:
            raise ValueError(
                f"The minimum partial-export wrapper needs {empty_cost} "
                f"{counter.count_kind}, including its omission notice; "
                f"the available budget is {budget}."
            )

        estimated_cost = _estimate_base_cost(
            blocks, sources, resolved, counter, empty_cost
        )
        for candidate in candidates:
            if candidate.block_id in selected:
                continue
            bundle = _selection_bundle(
                candidate,
                by_id,
                neighbors,
                heading_blocks,
                footnotes_by_unit,
                footnotes_by_source,
                resolved.mode,
            )
            new_ids = {block.block_id for block in bundle} - selected
            if not new_ids:
                continue
            if exact_attempts:
                proposed = selected | new_ids
                proposed_blocks = sorted(
                    (by_id[block_id] for block_id in proposed),
                    key=lambda block: block.order,
                )
                content = _render_content(
                    proposed_blocks,
                    sources,
                    resolved.mode,
                    omitted_count=len(blocks) - len(proposed),
                    unavailable_count=unavailable_count,
                    experimental_compression=resolved.compression == "llmlingua2",
                )
                fits = counter.count(content) <= budget
                delta = 0
            else:
                delta = _estimate_bundle_cost(
                    [by_id[block_id] for block_id in new_ids],
                    new_ids,
                    selected_sources,
                    selected_units,
                    selected_sections,
                    sources,
                    resolved,
                    counter,
                )
                fits = estimated_cost + delta <= budget
            if fits:
                selected.update(new_ids)
                accepted_groups.append(new_ids)
                if not exact_attempts:
                    estimated_cost += delta
                    for block_id in new_ids:
                        block = by_id[block_id]
                        selected_sources.add(block.source_id)
                        selected_units.add(block.unit_id)
                        if resolved.mode == "balanced":
                            selected_sections.add(
                                (block.source_id, block.unit_id, block.heading_path)
                            )
            else:
                failed_budget.update(new_ids)

    selected_blocks = sorted(
        (by_id[block_id] for block_id in selected),
        key=lambda block: block.order,
    )
    content = _render_content(
        selected_blocks,
        sources,
        resolved.mode,
        omitted_count=len(blocks) - len(selected),
        unavailable_count=unavailable_count,
        experimental_compression=resolved.compression == "llmlingua2",
    )
    output_tokens = counter.count(content)
    while output_tokens > budget and accepted_groups:
        removed_group = accepted_groups.pop()
        failed_budget.update(removed_group)
        selected = set().union(*accepted_groups) if accepted_groups else set()
        selected_blocks = sorted(
            (by_id[block_id] for block_id in selected),
            key=lambda block: block.order,
        )
        content = _render_content(
            selected_blocks,
            sources,
            resolved.mode,
            omitted_count=len(blocks) - len(selected),
            unavailable_count=unavailable_count,
            experimental_compression=resolved.compression == "llmlingua2",
        )
        output_tokens = counter.count(content)
    if output_tokens > budget:
        raise ValueError(
            f"The complete context export needs {output_tokens} "
            f"{counter.count_kind} but the available budget is {budget}."
        )

    selected_records = [_block_record(block) for block in selected_blocks]
    omitted_records: list[dict[str, Any]] = []
    for block in blocks:
        if block.block_id in selected:
            continue
        if block.block_id in failed_budget:
            single_content = _render_content(
                [block],
                sources,
                resolved.mode,
                omitted_count=0,
                unavailable_count=unavailable_count,
                experimental_compression=resolved.compression == "llmlingua2",
            )
            reason = (
                "oversize_block"
                if counter.count(single_content) > budget
                else "over_budget"
            )
        elif resolved.mode == "balanced" and block.kind == "heading":
            reason = "represented_in_outline"
        elif resolved.mode == "focused" and block.block_id not in considered:
            reason = "not_relevant"
        else:
            reason = "not_selected"
        record = _block_record(block)
        record["reason"] = reason
        omitted_records.append(record)
    omitted_records.extend(extraction_omissions)
    unresolved_references = _count_unresolved_footnotes(
        selected_blocks, footnotes_by_unit, footnotes_by_source
    )
    if unresolved_references:
        warnings.append(
            f"Could not resolve {unresolved_references} footnote reference(s) "
            "in selected evidence."
        )

    raw_tokens = counter.count(raw_text)
    cleaned_tokens = counter.count(cleaned_text)
    reduction = (
        round((raw_tokens - output_tokens) * 100 / raw_tokens, 2)
        if raw_tokens
        else 0.0
    )
    return {
        "content": content,
        "stats": {
            "raw_tokens": raw_tokens,
            "cleaned_tokens": cleaned_tokens,
            "output_tokens": output_tokens,
            "budget_tokens": budget,
            "tokenizer": resolved.tokenizer,
            "count_kind": counter.count_kind,
            "reduction_percent": reduction,
        },
        "sources": sources,
        "selected": selected_records,
        "omitted": omitted_records,
        "warnings": warnings,
        "outline": outline,
    }


def _validate_options(options: Mapping[str, Any] | None) -> ContextOptions:
    if options is None:
        values: dict[str, Any] = {}
    elif isinstance(options, Mapping):
        values = dict(options)
    else:
        raise TypeError("options must be a mapping or None.")
    unknown = set(values) - _ALLOWED_OPTIONS
    if unknown:
        names = ", ".join(sorted(map(str, unknown)))
        raise ValueError(f"Unknown context option(s): {names}.")

    defaults = asdict(ContextOptions())
    defaults.update(values)
    for key in _SAFE_INTEGER_FIELDS:
        value = defaults[key]
        if value is None and key == "context_window":
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer.")
        if key in {"max_tokens", "context_window"} and value <= 0:
            raise ValueError(f"{key} must be a positive integer.")
        if key in {"reserved_tokens", "answer_tokens"} and value < 0:
            raise ValueError(f"{key} must be a non-negative integer.")
    if not isinstance(defaults["tokenizer"], str) or defaults["tokenizer"] not in {
        "cl100k_base",
        "o200k_base",
        "utf8_bytes",
    }:
        raise ValueError("tokenizer must be cl100k_base, o200k_base, or utf8_bytes.")
    if not isinstance(defaults["mode"], str) or defaults["mode"] not in {
        "faithful",
        "balanced",
        "focused",
    }:
        raise ValueError("mode must be faithful, balanced, or focused.")
    if not isinstance(defaults["query"], str):
        raise ValueError("query must be a string.")
    if defaults["mode"] == "focused" and not defaults["query"].strip():
        raise ValueError("mode='focused' requires a non-empty query.")
    if not isinstance(defaults["compression"], str) or defaults["compression"] not in {
        "none",
        "llmlingua2",
    }:
        raise ValueError("compression must be none or llmlingua2.")
    rate = defaults["compression_rate"]
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        raise ValueError("compression_rate must be a number between 0 and 1.")
    if rate <= 0 or rate > 1 or (isinstance(rate, float) and not math.isfinite(rate)):
        raise ValueError("compression_rate must be greater than 0 and at most 1.")
    model = defaults["compressor_model"]
    if model is not None and not isinstance(model, (str, os.PathLike)):
        raise ValueError("compressor_model must be a local path string.")
    if model is not None:
        model = os.fspath(model)
    if defaults["context_window"] is not None:
        available = (
            defaults["context_window"]
            - defaults["reserved_tokens"]
            - defaults["answer_tokens"]
        )
        if available <= 0:
            raise ValueError(
                "context_window must exceed reserved_tokens + answer_tokens."
            )
    return ContextOptions(
        max_tokens=defaults["max_tokens"],
        tokenizer=defaults["tokenizer"],
        mode=defaults["mode"],
        query=defaults["query"],
        context_window=defaults["context_window"],
        reserved_tokens=defaults["reserved_tokens"],
        answer_tokens=defaults["answer_tokens"],
        compression=defaults["compression"],
        compression_rate=float(rate),
        compressor_model=model,
    )


def _make_budget(options: ContextOptions) -> tuple[int, Any]:
    available = options.max_tokens
    if options.context_window is not None:
        available = min(
            available,
            options.context_window - options.reserved_tokens - options.answer_tokens,
        )
    if available <= 0:
        raise ValueError("The available context budget must be positive.")
    counter = make_counter(options.tokenizer)
    minimum = counter.count(_MINIMUM_EXPORT)
    if minimum > available:
        raise ValueError(
            f"The minimum context wrapper needs {minimum} {counter.count_kind}; "
            f"the available budget is {available}."
        )
    return available, counter


def _normalize_extraction(
    extraction: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_sources = extraction.get("sources", [])
    raw_units = extraction.get("units", [])
    if not isinstance(raw_sources, (list, tuple)):
        raise ValueError("extraction.sources must be a list.")
    if not isinstance(raw_units, (list, tuple)):
        raise ValueError("extraction.units must be a list.")

    sources: list[dict[str, Any]] = []
    source_ids: set[str] = set()
    for index, source in enumerate(raw_sources, start=1):
        if not isinstance(source, Mapping):
            raise ValueError(f"extraction.sources[{index - 1}] must be a mapping.")
        source_id = _read_text(source.get("id")) or f"S{index}"
        if source_id in source_ids:
            continue
        source_ids.add(source_id)
        name = _read_text(source.get("name")) or source_id
        uri = _safe_uri(source.get("uri"), name)
        sources.append({"id": source_id, "name": name, "uri": uri})

    units: list[dict[str, Any]] = []
    unit_counts: dict[str, int] = {}
    for index, unit in enumerate(raw_units):
        if not isinstance(unit, Mapping):
            raise ValueError(f"extraction.units[{index}] must be a mapping.")
        source_id = _read_text(unit.get("source_id"))
        if not source_id:
            source_id = sources[0]["id"] if sources else "S1"
        if source_id not in source_ids:
            source_ids.add(source_id)
            sources.append(
                {"id": source_id, "name": source_id, "uri": source_id}
            )
        unit_counts[source_id] = unit_counts.get(source_id, 0) + 1
        unit_id = _read_text(unit.get("id")) or (
            f"{source_id}-U{unit_counts[source_id]:04d}"
        )
        units.append(
            {
                "id": unit_id,
                "source_id": source_id,
                "locator": _json_safe(unit.get("locator")),
                "raw": _read_text(unit.get("raw")),
                "text": _read_text(unit.get("text")),
                "text_present": isinstance(unit.get("text"), str),
            }
        )

    if not units:
        raw = _read_text(extraction.get("raw"))
        cleaned = _read_text(extraction.get("cleaned"))
        text_present = isinstance(extraction.get("cleaned"), str)
        fallback_text = cleaned if text_present else raw
        if fallback_text:
            if not sources:
                sources.append({"id": "S1", "name": "Input document", "uri": "input"})
            source_id = sources[0]["id"]
            units.append(
                {
                    "id": f"{source_id}-U0001",
                    "source_id": source_id,
                    "locator": "input",
                    "raw": raw or cleaned,
                    "text": fallback_text,
                    "text_present": text_present,
                }
            )
    return sources, units


def _normalize_omissions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    records: list[dict[str, Any]] = []
    for omission in value:
        if not isinstance(omission, Mapping):
            continue
        records.append(
            {
                str(key): _json_safe(item)
                for key, item in omission.items()
            }
        )
    return records


def _build_blocks(units: list[dict[str, Any]]) -> list[ContextBlock]:
    blocks: list[ContextBlock] = []
    unit_occurrences: dict[str, int] = {}
    order = 0
    for unit in units:
        unit_id = unit["id"]
        unit_occurrences[unit_id] = unit_occurrences.get(unit_id, 0) + 1
        suffix = (
            f"-D{unit_occurrences[unit_id]:02d}"
            if unit_occurrences[unit_id] > 1
            else ""
        )
        text = unit["text"] if unit["text_present"] else unit["raw"]
        parsed = _parse_markdown_unit(text, unit, suffix, order)
        blocks.extend(parsed)
        order += len(parsed)

    result: list[ContextBlock] = []
    for block in blocks:
        refs = tuple(
            dict.fromkeys(_FOOTNOTE_REF_RE.findall(block.text))
        )
        result.append(replace(block, footnote_refs=refs))
    return result


def _parse_markdown_unit(
    text: str,
    unit: Mapping[str, Any],
    duplicate_suffix: str,
    order_start: int,
) -> list[ContextBlock]:
    lines = text.splitlines(keepends=True)
    if text and not lines:
        lines = [text]
    if text and lines and not lines[-1].endswith(("\n", "\r")):
        pass
    blocks: list[ContextBlock] = []
    heading_stack: list[tuple[int, str]] = []
    heading_path: tuple[str, ...] = ()
    index = 0

    def add(start: int, end: int, kind: str, path: tuple[str, ...], footnote_id: str | None = None) -> None:
        nonlocal index
        block_text = "".join(lines[start:end]).rstrip("\r\n")
        if not block_text.strip():
            return
        index += 1
        block_id = f"{unit['id']}{duplicate_suffix}-B{index:04d}"
        blocks.append(
            ContextBlock(
                block_id=block_id,
                source_id=unit["source_id"],
                unit_id=unit["id"],
                locator=unit.get("locator"),
                heading_path=path,
                text=block_text,
                kind=kind,
                order=order_start + index,
                footnote_id=footnote_id,
            )
        )

    def body(line: str) -> str:
        return line.rstrip("\r\n")

    line_index = 0
    while line_index < len(lines):
        current = body(lines[line_index])
        if not current.strip():
            line_index += 1
            continue

        heading_match = _HEADING_RE.match(current)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            heading_path = tuple(name for _, name in heading_stack)
            add(line_index, line_index + 1, "heading", heading_path)
            line_index += 1
            continue

        if line_index + 1 < len(lines) and _SETEXT_RE.match(body(lines[line_index + 1])):
            marker = _SETEXT_RE.match(body(lines[line_index + 1]))
            assert marker is not None
            level = 1 if marker.group(1).startswith("=") else 2
            title = current.strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            heading_path = tuple(name for _, name in heading_stack)
            add(line_index, line_index + 2, "heading", heading_path)
            line_index += 2
            continue

        fence_match = _FENCE_RE.match(current)
        if fence_match:
            marker = fence_match.group(1)
            char = marker[0]
            minimum = len(marker)
            closing_re = re.compile(
                rf"^\s{{0,3}}{re.escape(char)}{{{minimum},}}\s*$"
            )
            end = line_index + 1
            while end < len(lines) and not closing_re.match(body(lines[end])):
                end += 1
            if end < len(lines):
                end += 1
            add(line_index, end, "code", tuple(heading_path))
            line_index = end
            continue

        if _is_table_start(lines, line_index):
            end = line_index + 2
            while end < len(lines):
                row = body(lines[end])
                if not row.strip() or "|" not in row:
                    break
                end += 1
            end = _append_footnote_lines(lines, end)
            add(line_index, end, "table", tuple(heading_path))
            line_index = end
            continue

        if _FOOTNOTE_RE.match(current):
            end = line_index + 1
            while end < len(lines):
                following = body(lines[end])
                if following.startswith((" ", "\t")) and following.strip():
                    end += 1
                else:
                    break
            note_match = _FOOTNOTE_RE.match(current)
            note_id = note_match.group(1) if note_match else None
            add(line_index, end, "footnote", tuple(heading_path), note_id)
            line_index = end
            continue

        if current.startswith(("    ", "\t")):
            end = line_index + 1
            while end < len(lines):
                following = body(lines[end])
                if following.startswith(("    ", "\t")):
                    end += 1
                elif not following.strip() and end + 1 < len(lines) and body(lines[end + 1]).startswith(("    ", "\t")):
                    end += 1
                else:
                    break
            end = _append_footnote_lines(lines, end)
            add(line_index, end, "code", tuple(heading_path))
            line_index = end
            continue

        if _LIST_RE.match(current):
            end = line_index + 1
            base_indent = len(current) - len(current.lstrip(" "))
            while end < len(lines):
                following = body(lines[end])
                if not following.strip():
                    next_line = body(lines[end + 1]) if end + 1 < len(lines) else ""
                    next_indent = len(next_line) - len(next_line.lstrip(" "))
                    if _LIST_RE.match(next_line) or (next_line.strip() and next_indent > base_indent):
                        end += 1
                        continue
                    break
                indent = len(following) - len(following.lstrip(" "))
                if _LIST_RE.match(following) or indent > base_indent or following.startswith("\t"):
                    end += 1
                    continue
                break
            end = _append_footnote_lines(lines, end)
            add(line_index, end, "list", tuple(heading_path))
            line_index = end
            continue

        end = line_index + 1
        while end < len(lines):
            following = body(lines[end])
            if not following.strip():
                break
            if (
                _HEADING_RE.match(following)
                or _FENCE_RE.match(following)
                or _LIST_RE.match(following)
                or _FOOTNOTE_RE.match(following)
                or following.startswith(("    ", "\t"))
                or _is_table_start(lines, end)
            ):
                break
            if end + 1 < len(lines) and _SETEXT_RE.match(body(lines[end + 1])):
                break
            end += 1
        add(line_index, end, "paragraph", tuple(heading_path))
        line_index = end

    return blocks


def _is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    current = lines[index].rstrip("\r\n")
    following = lines[index + 1].rstrip("\r\n")
    return "|" in current and _TABLE_RULE_RE.match(following) is not None


def _append_footnote_lines(lines: list[str], index: int) -> int:
    end = index
    while end < len(lines) and _FOOTNOTE_RE.match(lines[end].rstrip("\r\n")):
        end += 1
        while end < len(lines):
            continuation = lines[end].rstrip("\r\n")
            if continuation.startswith((" ", "\t")) and continuation.strip():
                end += 1
            else:
                break
    return end


def _candidate_blocks(
    blocks: list[ContextBlock], options: ContextOptions
) -> tuple[list[ContextBlock], set[str]]:
    if options.mode == "faithful":
        return list(blocks), {block.block_id for block in blocks}
    if options.mode == "focused":
        ranked = ranked_blocks(blocks, options.query)
        return ranked, {block.block_id for block in ranked}

    grouped: OrderedDict[tuple[str, str, tuple[str, ...]], list[ContextBlock]] = OrderedDict()
    for block in blocks:
        if block.kind == "heading":
            continue
        key = (block.source_id, block.unit_id, block.heading_path)
        grouped.setdefault(key, []).append(block)
    sections = list(grouped.values())
    candidates: list[ContextBlock] = []
    max_depth = max((len(section) for section in sections), default=0)
    for depth in range(max_depth):
        for section in sections:
            if depth < len(section):
                candidates.append(section[depth])
    return candidates, {block.block_id for block in candidates}


def _build_selection_indexes(
    blocks: list[ContextBlock],
) -> tuple[
    dict[str, list[str]],
    dict[tuple[str, str, tuple[str, ...]], list[str]],
    dict[tuple[str, str, str], list[str]],
    dict[tuple[str, str], list[str]],
]:
    neighbors: dict[str, list[str]] = {}
    headings: dict[tuple[str, str, tuple[str, ...]], list[str]] = {}
    footnotes_by_unit: dict[tuple[str, str, str], list[str]] = {}
    footnotes_by_source: dict[tuple[str, str], list[str]] = {}
    for index, block in enumerate(blocks):
        if block.kind == "heading":
            key = (block.source_id, block.unit_id, block.heading_path)
            headings.setdefault(key, []).append(block.block_id)
        if block.footnote_id:
            unit_key = (block.source_id, block.unit_id, block.footnote_id)
            source_key = (block.source_id, block.footnote_id)
            footnotes_by_unit.setdefault(unit_key, []).append(block.block_id)
            footnotes_by_source.setdefault(source_key, []).append(block.block_id)
        adjacent: list[str] = []
        for neighbor_index in (index - 1, index + 1):
            if not 0 <= neighbor_index < len(blocks):
                continue
            neighbor = blocks[neighbor_index]
            if (
                neighbor.source_id == block.source_id
                and neighbor.unit_id == block.unit_id
                and neighbor.heading_path == block.heading_path
            ):
                adjacent.append(neighbor.block_id)
        neighbors[block.block_id] = adjacent
    return neighbors, headings, footnotes_by_unit, footnotes_by_source


def _count_unresolved_footnotes(
    blocks: list[ContextBlock],
    by_unit: dict[tuple[str, str, str], list[str]],
    by_source: dict[tuple[str, str], list[str]],
) -> int:
    unresolved = 0
    for block in blocks:
        for reference in block.footnote_refs:
            if not by_unit.get((block.source_id, block.unit_id, reference)) and not by_source.get(
                (block.source_id, reference)
            ):
                unresolved += 1
    return unresolved


def _selection_bundle(
    candidate: ContextBlock,
    by_id: dict[str, ContextBlock],
    neighbors: dict[str, list[str]],
    heading_blocks: dict[tuple[str, str, tuple[str, ...]], list[str]],
    footnotes_by_unit: dict[tuple[str, str, str], list[str]],
    footnotes_by_source: dict[tuple[str, str], list[str]],
    mode: str,
) -> list[ContextBlock]:
    included = {candidate.block_id}
    if mode in {"faithful", "focused"}:
        for depth in range(1, len(candidate.heading_path) + 1):
            key = (
                candidate.source_id,
                candidate.unit_id,
                candidate.heading_path[:depth],
            )
            included.update(heading_blocks.get(key, ()))
    if mode == "focused":
        included.update(neighbors.get(candidate.block_id, ()))
    pending = [
        (reference, by_id[block_id].source_id, by_id[block_id].unit_id)
        for block_id in included
        for reference in by_id[block_id].footnote_refs
    ]
    while pending:
        reference, source_id, unit_id = pending.pop()
        unit_matches = footnotes_by_unit.get(
            (source_id, unit_id, reference), []
        )
        definitions = unit_matches or footnotes_by_source.get(
            (source_id, reference), []
        )
        for definition_id in definitions:
            if definition_id in included:
                continue
            included.add(definition_id)
            definition = by_id[definition_id]
            pending.extend(
                (ref, definition.source_id, definition.unit_id)
                for ref in definition.footnote_refs
            )
    return sorted((by_id[block_id] for block_id in included), key=lambda block: block.order)


def _make_outline(blocks: list[ContextBlock]) -> list[dict[str, Any]]:
    outline: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for block in blocks:
        if not block.heading_path:
            continue
        key = (block.source_id, block.unit_id, block.heading_path)
        if key in seen:
            continue
        seen.add(key)
        outline.append(
            {
                "source_id": block.source_id,
                "unit_id": block.unit_id,
                "locator": _json_safe(block.locator),
                "path": list(block.heading_path),
            }
        )
    return outline


def _render_content(
    blocks: Iterable[ContextBlock],
    sources: list[dict[str, Any]],
    mode: str,
    omitted_count: int = 0,
    unavailable_count: int = 0,
    experimental_compression: bool = False,
) -> str:
    ordered = sorted(blocks, key=lambda block: block.order)
    pieces = [_WRAPPER]
    if experimental_compression:
        pieces.append(
            "> Learned compression is experimental and lossy; verify facts against the source."
        )
    notices: list[str] = []
    if omitted_count:
        notices.append(
            f"{omitted_count} source blocks omitted by budget or relevance"
        )
    if unavailable_count:
        notices.append(f"{unavailable_count} extracted unit(s) unavailable")
    if notices:
        pieces.append("> Excerpt: " + "; ".join(notices) + ".")
    if mode == "balanced" and ordered:
        outline_rows: list[dict[str, Any]] = []
        seen_outline: set[tuple[str, str, tuple[str, ...]]] = set()
        seen_overview: set[tuple[str, str, tuple[str, ...]]] = set()
        overview_blocks: list[ContextBlock] = []
        for block in ordered:
            key = (block.source_id, block.unit_id, block.heading_path)
            if block.heading_path and key not in seen_outline:
                seen_outline.add(key)
                outline_rows.append(
                    {
                        "source_id": block.source_id,
                        "unit_id": block.unit_id,
                        "locator": _json_safe(block.locator),
                        "path": list(block.heading_path),
                    }
                )
            if block.kind != "heading" and key not in seen_overview:
                seen_overview.add(key)
                overview_blocks.append(block)
        if outline_rows:
            outline_data = "\n".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":"))
                for row in outline_rows
            )
            pieces.append("## Outline\n" + outline_data)
        if overview_blocks:
            overview_data = "\n\n".join(
                _render_block(block) for block in overview_blocks
            )
            pieces.append("## Overview\n" + overview_data)

    pieces.append("## Evidence")
    if ordered:
        pieces.extend(_render_block(block) for block in ordered)

    if ordered:
        cited_ids = {block.source_id for block in ordered}
        cited_units = {block.unit_id: block for block in ordered}
        map_lines: list[str] = []
        for source in sources:
            if source["id"] in cited_ids:
                map_lines.append(
                    json.dumps(source, ensure_ascii=False, separators=(",", ":"))
                )
        for unit_id, block in sorted(
            cited_units.items(), key=lambda item: item[1].order
        ):
            map_lines.append(
                json.dumps(
                    {
                        "unit": unit_id,
                        "source": block.source_id,
                        "locator": _json_safe(block.locator),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        pieces.append("## Source map\n" + "\n".join(map_lines))
    return "\n\n".join(pieces)


def _render_block(block: ContextBlock) -> str:
    citation = f"> [{block.block_id}]"
    if block.heading_path and block.kind != "heading":
        citation += " — " + " > ".join(block.heading_path)
    return f"{citation}\n{block.text}"


def _estimate_base_cost(
    blocks: list[ContextBlock],
    sources: list[dict[str, Any]],
    options: ContextOptions,
    counter: Any,
    empty_cost: int,
) -> int:
    # The all-omitted notice is the conservative base. A margin covers BPE
    # boundary changes around its changing count as blocks are selected.
    return empty_cost + (2 if counter.count_kind == "exact_tokens" else 0)


def _estimate_bundle_cost(
    new_blocks: list[ContextBlock],
    new_ids: set[str],
    selected_sources: set[str],
    selected_units: set[str],
    selected_sections: set[tuple[str, str, tuple[str, ...]]],
    sources: list[dict[str, Any]],
    options: ContextOptions,
    counter: Any,
) -> int:
    """Additive, conservative estimate; final export is always recounted exactly."""
    cost = 0
    boundary_slack = 1 if counter.count_kind == "exact_tokens" else 0
    seen_sources = set(selected_sources)
    seen_units = set(selected_units)
    seen_sections = set(selected_sections)
    ordered_new = sorted(new_blocks, key=lambda block: block.order)
    new_overview_sections: set[tuple[str, str, tuple[str, ...]]] = set()
    for block in ordered_new:
        citation = f"> [{block.block_id}]"
        if block.heading_path and block.kind != "heading":
            citation += " — " + " > ".join(block.heading_path)
        fragment = f"\n\n{citation}\n{block.text}"
        # Tiktoken can merge across a fragment boundary; one token of slack per
        # fragment prevents those local boundary effects from granting budget.
        cost += counter.count(fragment) + boundary_slack
        if options.mode == "balanced" and block.kind != "heading":
            section_key = (block.source_id, block.unit_id, block.heading_path)
            if section_key not in seen_sections and section_key not in new_overview_sections:
                cost += counter.count(fragment) + boundary_slack
                new_overview_sections.add(section_key)

    source_by_id = {source["id"]: source for source in sources}
    for block in ordered_new:
        if block.source_id not in seen_sources:
            row = json.dumps(
                source_by_id.get(
                    block.source_id,
                    {"id": block.source_id, "name": block.source_id, "uri": block.source_id},
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if not seen_sources:
                cost += counter.count("\n\n## Source map") + boundary_slack
            cost += counter.count("\n" + row) + boundary_slack
            seen_sources.add(block.source_id)
        if block.unit_id not in seen_units:
            row = json.dumps(
                {
                    "unit": block.unit_id,
                    "source": block.source_id,
                    "locator": _json_safe(block.locator),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            cost += counter.count("\n" + row) + boundary_slack
            seen_units.add(block.unit_id)
        if options.mode == "balanced" and block.heading_path:
            section_key = (block.source_id, block.unit_id, block.heading_path)
            if section_key not in seen_sections:
                row = json.dumps(
                    {
                        "source_id": block.source_id,
                        "unit_id": block.unit_id,
                        "locator": _json_safe(block.locator),
                        "path": list(block.heading_path),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if not any(section[2] for section in seen_sections):
                    cost += counter.count("\n\n## Outline") + boundary_slack
                cost += counter.count("\n" + row) + boundary_slack
                seen_sections.add(section_key)
        elif options.mode == "balanced":
            seen_sections.add((block.source_id, block.unit_id, block.heading_path))
    return cost


def _safe_fence(text: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"\x60+", text)), default=0)
    return "\x60" * max(3, longest + 1)


def _block_record(block: ContextBlock) -> dict[str, Any]:
    record = {
        "block_id": block.block_id,
        "source_id": block.source_id,
        "unit_id": block.unit_id,
        "locator": _json_safe(block.locator),
        "section": list(block.heading_path),
        "kind": block.kind,
    }
    if block.footnote_id:
        record["footnote_id"] = block.footnote_id
    return record


def _safe_uri(value: Any, name: str) -> str:
    uri = _read_text(value)
    if not uri:
        return name
    parsed = urlsplit(uri)
    if parsed.scheme == "file":
        return os.path.basename(unquote(parsed.path)) or name
    if not parsed.scheme and (
        uri.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", uri)
    ):
        return PurePath(uri.replace("\\", "/")).name or name
    return uri


def _read_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return str(value)
