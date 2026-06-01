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
    r'|copyright\s+©'
    r'|powered\s+by\s+gitbook'
    r'|gitbook\s+assistant'
    r'|was\s+this\s+helpful'
    r'|on\s+this\s+page'
    r'|good\s+(?:morning|afternoon|evening)'
    r'|i\'m\s+here\s+to\s+help'
    r'|what\s+is\s+this\s+page\s+about'
    r'|what\s+should\s+i\s+read\s+next'
    r'|can\s+you\s+give\s+an\s+example'
    r'|ai\s+based\s+on\s+your\s+context'
    r'|go\s+to\s+(?:website|homepage|home)'
    r'|open\s+menu\s+open\s+navigation'
    r'|expand\s+user\s+menu'
    r'|open\s+settings\s+menu'
    r'|sign\s+up\s+for\s+reddit'
    r'|log\s+in\s+to\s+reddit'
    r'|go\s+to\s+reddit\s+home'
    r'|new\s+to\s+reddit'
    r'|create\s+your\s+account'
    r'|reddit\s+uses\s+cookies'
    r'|reddit\s+rules'
    r'|reddit,?\s+inc\.?\s+©'
    r'|open\s+comment\s+sort\s+options'
    r'|more\s+replies'
    r'|\d+\s+more\s+repl(?:y|ies)'
    r'|view\s+post\s+in'
    r'|see\s+more\s+see\s+fewer'
    r'|expand\s+navigation\s+collapse\s+navigation'
    r'|accept\s+all\s+reject\s+optional\s+cookies'
    r'|anyone\s+can\s+view,?\s+post'
    r'|sign\s+(?:in|up)\s+to\s+(?:read|comment|continue|post)'
    r'|log\s+in\s+to\s+(?:comment|continue|view)'
    r'|continue\s+with\s+(?:email|google|apple|phone)'
    r'|by\s+continuing,?\s+you\s+agree'
    r'|prevent\s+fraud\s+and\s+abuse'
    r'|monitor\s+site\s+usage'
    r'|personalize\s+your\s+recommendations'
    r'|collapse\s+video\s+player'
    r'|sort\s+by',
    re.IGNORECASE,
)

# A line that is only a markdown link (optionally preceded by a list marker)
_BARE_LINK_RE = re.compile(r'^\s*[-*]?\s*\[([^\]]*)\]\([^)]+\)\s*$')

# A line containing ONLY markdown links (possibly multiple, no other text)
_LINKS_ONLY_LINE_RE = re.compile(
    r'^\s*(?:\[[^\]]*\]\([^)]+\)\s*)+$'
)

# A line that is only a markdown image
_BARE_IMAGE_RE = re.compile(r'^\s*[-*]?\s*!\[[^\]]*\]\([^)]+\)\s*$')

# List item containing only images
_IMAGE_ONLY_LIST_ITEM_RE = re.compile(
    r'^\s*[-*]\s+(?:!\[[^\]]*\]\([^)]+\)\s*)+$'
)

# Strips markdown image and link syntax
_MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\([^)]*\)')

# crawl4ai URL header: "## https://..."
_CRAWL_URL_HEADER_RE = re.compile(r'^##\s+https?://\S+\s*$', re.MULTILINE)

# Reddit timestamp link: "• [ 20d ago ](url)"
_REDDIT_TIMESTAMP_LINK_RE = re.compile(
    r'•\s*\[\s*(?:Edited\s+)?\d+[smhdwy]\s+ago\s*\]\([^)]*\)',
    re.IGNORECASE
)

# Plain timestamp bullet: "• 20d ago"
_REDDIT_PLAIN_TIMESTAMP_RE = re.compile(
    r'•\s+(?:Edited\s+)?\d+[smhdwy]\s+ago',
    re.IGNORECASE
)

# Reddit user link line
_REDDIT_USER_META_RE = re.compile(
    r'^\s*\[\s*\w[\w\s]*\]\(https?://www\.reddit\.com/user/[^)]+\)'
    r'(?:\s*•\s*\[\s*(?:Edited\s+)?\d+[smhdwy]\s+ago\s*\]\([^)]*\))?\s*$',
    re.IGNORECASE
)

# Sort option list items or standalone
_SORT_ITEM_RE = re.compile(
    r'^\s*[-*]?\s*(?:Best|Top|New|Controversial|Old|Q&A)\s*$',
    re.IGNORECASE
)

# Standalone UI labels
_UI_LABEL_RE = re.compile(
    r'^\s*(?:Share|Promoted|Sign\s+Up|Learn\s+More|Collapse\s+video\s+player)\s*$',
    re.IGNORECASE
)

# Reddit upvote/comment count lines
_REDDIT_COUNT_RE = re.compile(
    r'^\s*\d+\s*(?:upvotes?|comments?|points?|replies).*$', re.IGNORECASE
)

# Bare domain line
_BARE_DOMAIN_RE = re.compile(r'^\s*[\w\-]+\.(?:com|co\.uk|co|io|net|org|app)\s*$')

# Subreddit standalone line
_REDDIT_SUB_LINE_RE = re.compile(r'^\s*r/\w+\s*$')

# Any line containing "Promoted" as a link label
_PROMOTED_LINE_RE = re.compile(r'\[\s*Promoted\s*\]', re.IGNORECASE)

# Reddit badge/award text labels (after image stripped)
_REDDIT_BADGE_TEXT_RE = re.compile(
    r'^\s*(?:Top\s+\d+%\s+Commenter|Verified|Moderator|'
    r'Gold|Silver|Platinum|Helpful|Wholesome|'
    r'Rocket\s+Like|Bravo!|Take\s+My\s+Energy)\s*$',
    re.IGNORECASE
)

# Reddit badge/award images
_REDDIT_BADGE_RE = re.compile(
    r'!\[(?:Profile\s+Badge|[^\]]*(?:award|badge|flair))[^\]]*\]\([^)]*\)',
    re.IGNORECASE
)

_KNOWN_ICONS = {
    'bars', 'xmark', 'circle', 'search', 'close', 'menu', 'more',
    'chevron', 'up', 'down', 'left', 'right',
    'sun', 'bright', 'desktop', 'moon', 'block', 'quote', 'question',
    'hashtag', 'clipboard', 'list', 'file', 'lines', 'ellipsis', 'send',
    'gitbook', 'ask', 'circle-xmark', 'chevron-up', 'chevron-down',
    'chevron-left', 'chevron-right', 'sun-bright', 'block-quote',
    'question-circle', 'clipboard-list', 'file-lines',
}


def _is_link_only_line(line: str) -> bool:
    return bool(_BARE_LINK_RE.match(line))


def _is_links_only_line(line: str) -> bool:
    """Catches lines with multiple bare links and no other text."""
    return bool(_LINKS_ONLY_LINE_RE.match(line))


def _is_image_only_line(line: str) -> bool:
    if _BARE_IMAGE_RE.match(line):
        return True
    if _IMAGE_ONLY_LIST_ITEM_RE.match(line):
        return True
    return False


def _is_keyboard_shortcut_line(line: str) -> bool:
    stripped = line.strip()
    return bool(re.match(r'^[`⌘\s]*(Ctrl|Cmd|Alt|Shift|⌘)[`\w\s\+]*$', stripped, re.IGNORECASE))


def _is_reddit_chrome_line(line: str) -> bool:
    if _REDDIT_USER_META_RE.match(line):
        return True
    if _SORT_ITEM_RE.match(line):
        return True
    if _UI_LABEL_RE.match(line):
        return True
    if _REDDIT_COUNT_RE.match(line):
        return True
    if _BARE_DOMAIN_RE.match(line):
        return True
    if _REDDIT_SUB_LINE_RE.match(line):
        return True
    if _PROMOTED_LINE_RE.search(line):
        return True
    if _REDDIT_BADGE_TEXT_RE.match(line):
        return True
    # Lines that are only timestamp links after stripping
    stripped = _REDDIT_TIMESTAMP_LINK_RE.sub('', line).strip().strip('•').strip()
    if not stripped:
        return True
    return False


def _is_chrome_line(line: str) -> bool:
    stripped = _MD_IMAGE_RE.sub('', line)
    stripped = _MD_LINK_RE.sub(r'\1', stripped)
    stripped = re.sub(r'[`⌘\|\(\)\[\]\/•]', ' ', stripped)
    tokens = re.split(r'\s+', stripped.strip())
    tokens = [t.lower() for t in tokens if t]
    if not tokens:
        return True
    for token in tokens:
        if token in _KNOWN_ICONS:
            continue
        remainder = token
        for icon in sorted(_KNOWN_ICONS, key=len, reverse=True):
            remainder = remainder.replace(icon.replace('-', ''), '').replace(icon, '')
        remainder = re.sub(r'[-]', '', remainder)
        if remainder:
            return False
    return True


def _strip_crawl_url_headers(text: str) -> str:
    return _CRAWL_URL_HEADER_RE.sub('', text)


def _strip_inline_timestamps(text: str) -> str:
    text = _REDDIT_TIMESTAMP_LINK_RE.sub('', text)
    text = _REDDIT_PLAIN_TIMESTAMP_RE.sub('', text)
    return text


def _strip_reddit_badges(text: str) -> str:
    return _REDDIT_BADGE_RE.sub('', text)


def _strip_promoted_blocks(text: str) -> str:
    promoted_block = re.compile(
        r'[^\n]*\[\s*Promoted\s*\][^\n]*\n(?:[^\n]+\n){0,3}',
        re.IGNORECASE
    )
    return promoted_block.sub('', text)


def _strip_pre_heading_chrome(text: str) -> str:
    for m in re.finditer(r'^(#{1,6})\s+(.+)$', text, re.MULTILINE):
        heading_text = m.group(2).strip()
        if re.match(r'https?://\S+$', heading_text):
            continue
        start = m.start()
        pre = text[:start]
        if pre.count('\n') <= 2:
            return text
        return text[start:]
    return text


def _strip_post_content_tail(text: str) -> str:
    tail_triggers = [
        r'reddit uses cookies',
        r'accept all\s+reject optional cookies',
        r'expand navigation collapse navigation',
        r'anyone can view, post',
        r'reddit rules',
        r'privacy policy.*user agreement',
        r'related answers',
        r'view post in\s*\n.*français',
        r'\* \* \*\s*\n.*\d+\s*(?:upvotes?|comments?)',
    ]
    combined = re.compile('|'.join(tail_triggers), re.IGNORECASE | re.DOTALL)
    match = combined.search(text)
    if match:
        text = text[:match.start()].rstrip()
    return text


def _extract_code_blocks(text: str):
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
    result = []
    i = 0
    n = len(lines)

    while i < n:
        j = i
        link_count = 0
        while j < n and (not lines[j].strip() or _is_link_only_line(lines[j])):
            if _is_link_only_line(lines[j]):
                link_count += 1
            j += 1

        if link_count >= 3:
            i = j
        else:
            result.append(lines[i])
            i += 1

    return result


def _remove_boilerplate_lines(lines: list) -> list:
    result = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append(line)
            continue
        if _is_image_only_line(line):
            continue
        if _is_keyboard_shortcut_line(line):
            continue
        if _is_reddit_chrome_line(line):
            continue
        if _is_links_only_line(line):
            continue
        if _is_chrome_line(line):
            continue
        if len(stripped) < 200 and _BOILERPLATE_RE.search(stripped):
            continue
        result.append(line)
    return result


def _deduplicate_paragraphs(text: str) -> str:
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
    if not markdown:
        return markdown

    text, code_blocks = _extract_code_blocks(markdown)

    text = _strip_post_content_tail(text)
    text = _strip_promoted_blocks(text)
    text = _strip_crawl_url_headers(text)
    text = _strip_inline_timestamps(text)
    text = _strip_reddit_badges(text)
    text = _strip_pre_heading_chrome(text)

    lines = text.split('\n')
    lines = _remove_boilerplate_lines(lines)
    lines = _remove_nav_blocks(lines)
    text = '\n'.join(lines)

    text = _deduplicate_paragraphs(text)

    text = re.sub(r'\n{3,}', '\n\n', text)
    text = '\n'.join(line.rstrip() for line in text.split('\n'))
    text = _restore_code_blocks(text, code_blocks)

    return text.strip()
