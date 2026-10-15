"""Public extraction API and backwards-compatible legacy wrapper."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from pipeline.extraction import ConversionError, _default_ocr_pdf_page, extract_local
from pipeline.normaliser import _split_protected, clean


MAX_PAGES = 25
# Keep this object stable: the existing Flask /progress route returns it directly.
crawl_progress = {"pages_done": 0, "pages_queued": 0}
_ocr_pdf_page = _default_ocr_pdf_page


_BODY_START_RE = re.compile(
    r"(?im)^ {0,3}(?:#{1,6}\s*)?(?:chapter|part|book)\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)\b"
)


def _validate_options(source, is_url, depth, profile, trim_body, allow_remote, use_browser, progress):
    if source is None or source == "" or source == [] or source == ():
        raise ConversionError("Source must not be empty")
    if type(is_url) is not bool:
        raise ConversionError("is_url must be true or false")
    if type(depth) is not int or depth < 1 or depth > MAX_PAGES:
        raise ConversionError(f"depth must be an integer from 1 to {MAX_PAGES}")
    if not isinstance(profile, str) or profile not in {"document", "web", "reddit"}:
        raise ConversionError("profile must be 'document', 'web', or 'reddit'")
    if type(trim_body) is not bool:
        raise ConversionError("trim_body must be true or false")
    if type(allow_remote) is not bool:
        raise ConversionError("allow_remote must be true or false")
    if type(use_browser) is not bool:
        raise ConversionError("use_browser must be true or false")
    if progress is not None and not callable(progress):
        raise ConversionError("progress must be a callable or None")


def _body_trim(units: list[dict], warnings: list[str]) -> tuple[list[dict], list[dict]]:
    omitted = []
    by_source = {}
    for unit in units:
        by_source.setdefault(unit["source_id"], []).append(unit)
    for source_id, source_units in by_source.items():
        start_index = next(
            (
                index
                for index, unit in enumerate(source_units)
                if any(not protected and _BODY_START_RE.search(segment) for protected, segment in _split_protected(unit.get("text", "")))
            ),
            None,
        )
        if start_index is None:
            warnings.append(f"trim_body was requested for {source_id}, but no clear chapter or part heading was found; all units were retained.")
            continue
        for previous in source_units[:start_index]:
            previous["text"] = ""
            omitted.append({
                "unit_id": previous["id"],
                "source_id": source_id,
                "locator": previous["locator"],
                "reason": "Explicit body trim removed content before the first chapter or part heading.",
            })
    return [unit for unit in units if unit.get("text")], omitted


def _display_raw(units: list[dict], source: dict, source_count: int, is_url: bool) -> tuple[str, str]:
    raw_parts = []
    text_parts = []
    if source_count > 1:
        heading = f"## Source: {source['name']}"
        raw_parts.append(heading)
        text_parts.append(heading)
    for unit in units:
        raw = unit["raw"]
        text = unit["text"]
        if is_url:
            page_heading = f"## Page: {unit['locator']}"
            raw = f"{page_heading}\n\n{raw}" if raw else page_heading
            text = f"{page_heading}\n\n{text}" if text else page_heading
        raw_parts.append(raw)
        text_parts.append(text)
    return "\n\n---\n\n".join(part for part in raw_parts if part), "\n\n---\n\n".join(part for part in text_parts if part)


def extract_input(
    source,
    is_url: bool = False,
    depth: int = 1,
    *,
    profile: str = "document",
    trim_body: bool = False,
    allow_remote: bool = False,
    use_browser: bool = False,
    progress=None,
) -> dict:
    """Extract source content into faithful Markdown, cleaned text, and units."""
    _validate_options(source, is_url, depth, profile, trim_body, allow_remote, use_browser, progress)

    input_sources = list(source) if isinstance(source, (list, tuple)) else [source]
    if is_url and len(input_sources) != 1:
        raise ConversionError("A URL extraction accepts one URL at a time")
    if not input_sources:
        raise ConversionError("At least one source is required")

    sources = []
    units = []
    warnings = []
    source_metrics = []
    for source_index, input_source in enumerate(input_sources, start=1):
        source_id = f"S{source_index}"
        if is_url:
            if not isinstance(input_source, str):
                raise ConversionError("URL source must be a string")
            from pipeline.web_fetch import extract_url_pages

            url_units, url_warnings, metrics = extract_url_pages(
                input_source,
                source_id,
                depth=depth,
                profile=profile,
                allow_remote=allow_remote,
                use_browser=use_browser,
                progress_state=crawl_progress,
                progress=progress,
            )
            normalized_source_uri = url_units[0]["locator"]
            parsed = urlsplit(normalized_source_uri)
            source_name = parsed.hostname or normalized_source_uri
            source_meta = {"id": source_id, "name": source_name, "uri": normalized_source_uri}
        else:
            try:
                source_path = os.fspath(input_source)
            except TypeError as exc:
                raise ConversionError("Local source must be a path or a list of paths") from exc
            if not source_path.strip():
                raise ConversionError("Source must not be empty")
            source_name = Path(source_path).name or "document"
            source_meta = {"id": source_id, "name": source_name, "uri": source_name}
            url_units, url_warnings, metrics = extract_local(
                source_path,
                source_id,
                profile=profile,
                ocr_pdf_page=_ocr_pdf_page,
            )
        sources.append(source_meta)
        units.extend(url_units)
        warnings.extend(url_warnings)
        source_metrics.append({"source_id": source_id, **metrics})

    omissions = []
    selected_units = units
    if trim_body:
        selected_units, omissions = _body_trim(units, warnings)
    raw_parts = []
    cleaned_parts = []
    for source_meta in sources:
        this_source_units = [unit for unit in selected_units if unit["source_id"] == source_meta["id"]]
        original_source_units = [unit for unit in units if unit["source_id"] == source_meta["id"]]
        if len(sources) > 1:
            source_heading = f"## Source: {source_meta['name']}"
            raw_parts.append(source_heading)
            cleaned_parts.append(source_heading)
        if is_url:
            for unit in original_source_units:
                heading = f"## Page: {unit['locator']}"
                raw_parts.append(f"{heading}\n\n{unit['raw']}")
            for unit in this_source_units:
                if unit["text"]:
                    heading = f"## Page: {unit['locator']}"
                    cleaned_parts.append(f"{heading}\n\n{unit['text']}")
        else:
            raw_parts.extend(unit["raw"] for unit in original_source_units)
            cleaned_parts.extend(unit["text"] for unit in this_source_units)
    raw = "\n\n---\n\n".join(part for part in raw_parts if part)
    cleaned = "\n\n---\n\n".join(part for part in cleaned_parts if part)
    metrics = {
        "source_count": len(sources),
        "unit_count": len(units),
        "raw_characters": len(raw),
        "cleaned_characters": len(cleaned),
        "per_source": source_metrics,
    }
    return {
        "raw": raw,
        "cleaned": cleaned,
        "sources": sources,
        "units": units,
        "warnings": warnings,
        "omissions": omissions,
        "metrics": metrics,
    }


def _pdf_to_text(path: str) -> str:
    """Compatibility helper returning every PDF page in source order."""
    units, _warnings, _metrics = extract_local(path, "S1", ocr_pdf_page=_ocr_pdf_page)
    return "\n\n".join(unit["raw"] for unit in units)


def _epub_to_text(path: str) -> str:
    """Compatibility helper returning every EPUB spine document in order."""
    units, _warnings, _metrics = extract_local(path, "S1")
    return "\n\n".join(unit["raw"] for unit in units)


async def _crawl_recursive(seed_url: str, depth: int) -> str:
    """Compatibility coroutine backed by the local-first bounded URL extractor."""
    return extract_input(seed_url, is_url=True, depth=depth)["raw"]


def process_input(source, is_url: bool = False, depth: int = 1):
    """Legacy API returning an ``(raw, cleaned)`` tuple."""
    try:
        result = extract_input(source, is_url=is_url, depth=depth)
        return result["raw"], result["cleaned"]
    except (ConversionError, OSError, ValueError) as exc:
        message = str(exc).strip() or "Extraction failed"
        if not message.lower().startswith("error:"):
            message = f"Error: {message}"
        return message, message
