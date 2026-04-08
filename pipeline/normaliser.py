import re

_BOILERPLATE_RE = re.compile(
    r'accept\s+(?:all\s+)?cookies?'
    r'|(?:we\s+)?use\s+cookies'
    r'|cookie\s+(?:settings?|policy|notice|preferences?)'
    r'|skip\s+to\s+(?:main\s+)?content'
    r'|back\s+to\s+top'
    r'|subscribe\s+to\s+(?:our\s+)?newsletter'
    r'|follow\s+us\s+on'
    r'|share\s+this\s+(?:post|article|page)'
    r'|all\s+rights\s+reserved'
    r'|copyright\s+©',
    re.IGNORECASE,
)

# A line that is only a markdown link (optionally preceded by a list marker)
_BARE_LINK_RE = re.compile(r'^\s*[-*]?\s*\[([^\]]*)\]\([^)]+\)\s*$')


def _is_link_only_line(line: str) -> bool:
    return bool(_BARE_LINK_RE.match(line))


def _extract_code_blocks(text: str):
    """Replace fenced code blocks with null-byte-delimited placeholders."""
    blocks = {}
    idx = 0

    def replacer(m):
        nonlocal idx
        key = f'\x00CODEBLOCK{idx}\x00'
        blocks[key] = m.group(0)
        idx += 1
        return key

    text = re.sub(r'```[\s\S]*?```', replacer, text)
    return text, blocks


def _restore_code_blocks(text: str, blocks: dict) -> str:
    for key, val in blocks.items():
        text = text.replace(key, val)
    return text


def _remove_nav_blocks(lines: list) -> list:
    """Remove runs of 3+ consecutive bare-link lines (navigation menus).

    Blank lines between link lines are treated as part of the same run.
    Runs with fewer than 3 link lines are kept intact.
    """
    result = []
    i = 0
    n = len(lines)

    while i < n:
        # Scan forward through blank lines and link-only lines
        j = i
        link_count = 0
        while j < n and (not lines[j].strip() or _is_link_only_line(lines[j])):
            if _is_link_only_line(lines[j]):
                link_count += 1
            j += 1

        if link_count >= 3:
            # Nav block — skip the whole run
            i = j
        else:
            result.append(lines[i])
            i += 1

    return result


def _remove_boilerplate_lines(lines: list) -> list:
    """Drop short standalone lines that match known boilerplate phrases."""
    result = []
    for line in lines:
        stripped = line.strip()
        if len(stripped) < 200 and _BOILERPLATE_RE.search(stripped):
            continue
        result.append(line)
    return result


def _deduplicate_paragraphs(text: str) -> str:
    """Remove exact-duplicate paragraph blocks (keeps first occurrence)."""
    blocks = re.split(r'\n\n+', text)
    seen: set = set()
    deduped = []
    for block in blocks:
        key = block.strip()
        if not key:
            deduped.append(block)
            continue
        if key in seen:
            continue
        seen.add(key)
        deduped.append(block)
    return '\n\n'.join(deduped)


def clean(markdown: str) -> str:
    """Clean markdown: strip nav menus, boilerplate, duplicates, normalize whitespace.

    Code blocks are protected and restored unchanged.
    """
    if not markdown:
        return markdown

    text, code_blocks = _extract_code_blocks(markdown)

    lines = text.split('\n')
    lines = _remove_boilerplate_lines(lines)
    lines = _remove_nav_blocks(lines)
    text = '\n'.join(lines)

    text = _deduplicate_paragraphs(text)

    # Collapse 3+ consecutive blank lines to 2
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Strip trailing whitespace per line
    text = '\n'.join(line.rstrip() for line in text.split('\n'))

    text = _restore_code_blocks(text, code_blocks)

    return text.strip()
