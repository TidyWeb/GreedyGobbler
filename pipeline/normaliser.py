"""Conservative Markdown cleanup for extracted source material."""

from __future__ import annotations

import re


_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\n]*)$")
_UI_LINES = {
    "web": (
        re.compile(r"^accept\s+(?:all\s+)?cookies?$", re.I),
        re.compile(r"^cookie\s+(?:settings?|policy|notice|preferences?)$", re.I),
        re.compile(r"^skip\s+to\s+(?:main\s+)?content$", re.I),
        re.compile(r"^back\s+to\s+top$", re.I),
        re.compile(r"^subscribe\s+to\s+(?:our\s+)?newsletter$", re.I),
        re.compile(r"^follow\s+us\s+on\s+[\w .-]+$", re.I),
        re.compile(r"^share\s+this\s+(?:post|article|page)$", re.I),
        re.compile(r"^powered\s+by\s+gitbook$", re.I),
        re.compile(r"^gitbook\s+assistant$", re.I),
        re.compile(r"^was\s+this\s+helpful\??$", re.I),
        re.compile(r"^on\s+this\s+page$", re.I),
    ),
    "reddit": (
        re.compile(r"^accept\s+(?:all\s+)?cookies?$", re.I),
        re.compile(r"^reddit\s+uses\s+cookies$", re.I),
        re.compile(r"^sign\s+up\s+for\s+reddit$", re.I),
        re.compile(r"^log\s+in\s+to\s+reddit$", re.I),
        re.compile(r"^go\s+to\s+reddit\s+home$", re.I),
        re.compile(r"^open\s+comment\s+sort\s+options$", re.I),
        re.compile(r"^more\s+replies$", re.I),
        re.compile(r"^see\s+more\s+see\s+fewer$", re.I),
        re.compile(r"^\d+\s+(?:upvotes?|comments?|points?|replies)$", re.I),
        re.compile(r"^(?:Best|Top|New|Controversial|Old|Q&A)$", re.I),
    ),
}
_STRONG_UI_MARKERS = {
    "web": re.compile(r"^(?:accept\s+(?:all\s+)?cookies?|cookie\s+(?:settings?|policy|notice|preferences?)|powered\s+by\s+gitbook|gitbook\s+assistant)$", re.I),
    "reddit": re.compile(r"^(?:reddit\s+uses\s+cookies|sign\s+up\s+for\s+reddit|log\s+in\s+to\s+reddit|go\s+to\s+reddit\s+home)$", re.I),
}


def _line_content(line: str) -> str:
    return line.rstrip("\r\n")


def _is_indented_code(line: str) -> bool:
    content = _line_content(line)
    return content.startswith("\t") or content.startswith("    ")


def _split_protected(markdown: str) -> list[tuple[bool, str]]:
    """Split fenced and indented code from prose in one linear pass."""
    lines = markdown.splitlines(keepends=True)
    result: list[tuple[bool, str]] = []
    prose: list[str] = []
    index = 0

    def flush_prose() -> None:
        if prose:
            result.append((False, "".join(prose)))
            prose.clear()

    while index < len(lines):
        content = _line_content(lines[index])
        opening = _FENCE_OPEN_RE.match(content)
        if opening:
            flush_prose()
            fence = opening.group(1)
            marker = fence[0]
            length = len(fence)
            block = [lines[index]]
            index += 1
            close_re = re.compile(rf"^ {{0,3}}{re.escape(marker)}{{{length},}}\s*$")
            while index < len(lines):
                block.append(lines[index])
                if close_re.match(_line_content(lines[index])):
                    index += 1
                    break
                index += 1
            result.append((True, "".join(block)))
            continue

        if _is_indented_code(lines[index]):
            flush_prose()
            block: list[str] = []
            while index < len(lines):
                if _is_indented_code(lines[index]):
                    block.append(lines[index])
                    index += 1
                    continue
                if not _line_content(lines[index]).strip():
                    blank_start = index
                    while index < len(lines) and not _line_content(lines[index]).strip():
                        index += 1
                    if index < len(lines) and _is_indented_code(lines[index]):
                        block.extend(lines[blank_start:index])
                        continue
                    index = blank_start
                break
            result.append((True, "".join(block)))
            continue

        prose.append(lines[index])
        index += 1

    flush_prose()
    return result


def _is_ui_line(line: str, profile: str) -> bool:
    candidate = line.strip()
    return bool(candidate) and any(pattern.fullmatch(candidate) for pattern in _UI_LINES[profile])


def _bounded_ui_removal_indexes(lines: list[str], profile: str) -> set[int]:
    """Remove exact UI lines only in marked blocks at document edges."""
    if profile == "document" or not lines:
        return set()

    groups: list[tuple[int, int]] = []
    start = 0
    for index, line in enumerate(lines):
        if not line.strip():
            groups.append((start, index))
            start = index + 1
    groups.append((start, len(lines)))

    removable: set[int] = set()
    for group_index in (0, len(groups) - 1):
        group_start, group_end = groups[group_index]
        if group_start == group_end:
            continue
        exact_ui = [i for i in range(group_start, group_end) if _is_ui_line(lines[i], profile)]
        has_marker = any(_STRONG_UI_MARKERS[profile].fullmatch(lines[i].strip()) for i in exact_ui)
        if len(exact_ui) >= 2 and has_marker:
            removable.update(exact_ui)

    return removable


def _clean_prose(segment: str, profile: str) -> str:
    if not segment:
        return segment
    source_lines = segment.splitlines(keepends=True)
    lines = [_line_content(line) for line in source_lines]
    if not lines and segment:
        lines = [segment]
        source_lines = [segment]
    removed_indexes = _bounded_ui_removal_indexes(lines, profile)
    kept_indices = [index for index in range(len(lines)) if index not in removed_indexes]

    normalised: list[str] = []
    blank_count = 0
    for index in kept_indices:
        line = lines[index]
        original = source_lines[index]
        has_newline = original.endswith(("\n", "\r"))
        if not line.strip():
            blank_count += 1
            if blank_count <= 1:
                normalised.append("\n" if has_newline else "")
            continue
        blank_count = 0
        # Keep trailing spaces because Markdown uses two spaces for hard breaks.
        normalised.append(line + ("\n" if has_newline else ""))
    return "".join(normalised)


def _strip_outer_blank_lines(text: str) -> str:
    lines = text.splitlines(keepends=True)
    start = 0
    end = len(lines)
    while start < end and not _line_content(lines[start]).strip():
        start += 1
    while end > start and not _line_content(lines[end - 1]).strip():
        end -= 1
    return "".join(lines[start:end])


def clean(markdown: str, profile: str = "document") -> str:
    """Apply conservative cleanup while preserving Markdown evidence and structure.

    ``document`` keeps all non-whitespace content. ``web`` and ``reddit`` may
    remove exact, full-line interface labels when they appear together in a
    marked block at the start or end of a page.
    """
    if profile not in {"document", "web", "reddit"}:
        raise ValueError("profile must be 'document', 'web', or 'reddit'")
    if not markdown:
        return markdown

    pieces = []
    for protected, segment in _split_protected(markdown):
        pieces.append(segment if protected else _clean_prose(segment, profile))
    return _strip_outer_blank_lines("".join(pieces))
