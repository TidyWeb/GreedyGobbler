import os
import re
import asyncio
import subprocess
import tempfile
import requests
from urllib.parse import urlparse
from markitdown import MarkItDown
from crawl4ai import AsyncWebCrawler
from pipeline.normaliser import clean
import pytesseract
from PIL import Image
from pdf2image import convert_from_path
import pdfplumber
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

MAX_PAGES = 25

# Module-level progress tracker — safe for single-user local use
crawl_progress = {"pages_done": 0, "pages_queued": 0}

_md = MarkItDown()

MICROLINK_API = "https://api.microlink.io/"
MICROLINK_MIN_LENGTH = 300  # characters — below this we consider it a failure


def _fetch_with_microlink(url: str) -> str | None:
    """
    Attempt to fetch a URL via Microlink API and return clean markdown.
    Returns None if the fetch fails or returns insufficient content.
    """
    try:
        response = requests.get(
            MICROLINK_API,
            params={
                "url": url,
                "data.article.selector": "main, article, [role='main'], .content, .post, #content",
                "data.article.attr": "markdown",
                "meta": "false",
            },
            timeout=45,
        )
        data = response.json()

        if data.get("status") != "success":
            return None

        markdown = data.get("data", {}).get("article")

        if not markdown or len(markdown.strip()) < MICROLINK_MIN_LENGTH:
            # Fallback: try full-page extraction without selector
            response2 = requests.get(
                MICROLINK_API,
                params={
                    "url": url,
                    "data.article.attr": "markdown",
                    "meta": "false",
                },
                timeout=45,
            )
            data2 = response2.json()
            markdown = data2.get("data", {}).get("article")
            if not markdown or len(markdown.strip()) < MICROLINK_MIN_LENGTH:
                return None

        return markdown

    except Exception:
        return None


_EPUB_FRONT_MATTER_RE = re.compile(
    r'\b(?:cover|title|copyright|dedication|contents?|toc|nav|'
    r'foreword|preface|introduction|prologue|maps?|illustrations?|plates?|figures?|list\s+of\s+(?:maps|illustrations|figures|plates))\b',
    re.IGNORECASE,
)

_EPUB_BACK_MATTER_RE = re.compile(
    r'\b(?:acknowledgements?|acknowledgments?|notes?|endnotes?|footnotes?|'
    r'appendix|appendices|bibliography|index|about\s+the\s+author|'
    r'also\s+by|permissions?|illustration\s+credits?|credits?)\b',
    re.IGNORECASE,
)

_EPUB_BODY_START_RE = re.compile(
    r'\b(?:chapter|chap\.?|ch\.?\s*\d+|part\s+(?:one|two|three|four|five|\d+)|'
    r'book\s+(?:one|two|three|four|five|\d+))\b',
    re.IGNORECASE,
)

_EPUB_NOTE_RE = re.compile(r'(?:foot|end)?notes?|noteref|annotation', re.IGNORECASE)
_EPUB_PAGE_MARKER_RE = re.compile(r'^\s*(?:page\s+)?\d+\s*$', re.IGNORECASE)
_EPUB_CHAPTER_NUMBER_RE = re.compile(r'^\s*(?:chapter\s+)?(?:\d+|[ivxlcdm]+)\s*$', re.IGNORECASE)


def _epub_item_key(item) -> str:
    bits = [
        getattr(item, 'id', '') or '',
        getattr(item, 'file_name', '') or '',
        getattr(item, 'media_type', '') or '',
    ]
    return ' '.join(bits)


def _epub_spine_items(book):
    """Return document items in EPUB spine order, falling back to document order."""
    by_id = {item.get_id(): item for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
    ordered = []
    for entry in getattr(book, 'spine', []) or []:
        item_id = entry[0] if isinstance(entry, tuple) else entry
        item = by_id.get(item_id)
        if item is not None:
            ordered.append(item)
    if ordered:
        return ordered
    return list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))


def _epub_remove_non_body_nodes(soup: BeautifulSoup) -> None:
    """Remove EPUB chrome, notes, nav, scripts, and invisible non-body matter."""
    for tag in soup(['script', 'style', 'nav', 'sup']):
        tag.decompose()

    to_remove = []
    for tag in soup.find_all(True):
        if tag.attrs is None:
            continue
        classes = tag.get('class', [])
        if not isinstance(classes, list):
            classes = [str(classes)]
        attrs = ' '.join(
            str(value) for value in [
                tag.name,
                tag.get('id', '') or '',
                ' '.join(classes),
                tag.get('epub:type', '') or '',
                tag.get('role', '') or '',
            ]
        )
        if tag.name in {'aside'} or _EPUB_NOTE_RE.search(attrs):
            to_remove.append(tag)

    for tag in to_remove:
        tag.decompose()


def _epub_heading_text(soup: BeautifulSoup) -> str:
    for selector in ['h1', 'h2', 'h3', 'title']:
        node = soup.find(selector)
        if node:
            text = _normalise_inline_text(node.get_text(' ', strip=True))
            if text:
                return text
    return ''


def _epub_is_back_matter(item, soup: BeautifulSoup, text: str = '') -> bool:
    key = f"{_epub_item_key(item)} {_epub_heading_text(soup)}"
    return bool(_EPUB_BACK_MATTER_RE.search(key))


def _epub_is_front_matter(item, soup: BeautifulSoup, text: str = '') -> bool:
    key = f"{_epub_item_key(item)} {_epub_heading_text(soup)} {text[:1200]}"
    return bool(_EPUB_FRONT_MATTER_RE.search(key))


def _normalise_inline_text(text: str) -> str:
    text = text.replace('\xa0', ' ')
    text = re.sub(r'[ \t\r\f\v]+', ' ', text)
    text = re.sub(r'\s+([,.;:!?])', r'\1', text)
    text = re.sub(r'([\u2018\u201c\(\[])\s+', r'\1', text)
    return text.strip()


def _epub_block_to_markdown(tag) -> str:
    text = _normalise_inline_text(tag.get_text(' ', strip=True))
    if not text or _EPUB_PAGE_MARKER_RE.match(text):
        return ''

    name = tag.name.lower()
    if name in {'h1', 'h2'}:
        return f"# {text}"
    if name in {'h3', 'h4'}:
        return f"## {text}"
    if name in {'h5', 'h6'}:
        return f"### {text}"
    if name == 'blockquote':
        return '\n'.join(f"> {line}" for line in text.splitlines() if line.strip())
    if name == 'li':
        return f"- {text}"
    return text


def _epub_html_to_markdown(item) -> tuple[str, str]:
    soup = BeautifulSoup(item.get_content(), 'html.parser')
    _epub_remove_non_body_nodes(soup)
    heading = _epub_heading_text(soup)

    blocks = []
    body = soup.body or soup
    for tag in body.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'blockquote', 'li']):
        if tag.find_parent(['nav', 'aside']):
            continue
        block = _epub_block_to_markdown(tag)
        if block:
            blocks.append(block)

    if not blocks:
        fallback = _normalise_inline_text(body.get_text(' ', strip=True))
        if fallback:
            blocks.append(fallback)

    return heading, '\n\n'.join(blocks).strip()


def _epub_is_body_start(item, heading: str, text: str) -> bool:
    soup = BeautifulSoup(item.get_content(), 'html.parser')
    if _epub_is_front_matter(item, soup, text):
        return False
    key = f"{_epub_item_key(item)} {heading}"
    if _EPUB_BODY_START_RE.search(key):
        return True
    if _EPUB_CHAPTER_NUMBER_RE.match(heading or ''):
        return True
    # Do not start the body from long front-matter prose such as prefaces.
    # If an EPUB has no clear chapter marker, fallback selection handles it later.
    return False


def _epub_trim_front_and_back(sections: list[tuple[object, str, str]]) -> list[str]:
    body_started = False
    selected = []

    for item, heading, text in sections:
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        if body_started and _epub_is_back_matter(item, soup, text):
            break
        if not body_started:
            if _epub_is_front_matter(item, soup, text) and not _epub_is_body_start(item, heading, text):
                continue
            if not _epub_is_body_start(item, heading, text):
                continue
            body_started = True
        selected.append(text)

    if selected:
        return selected

    # Conservative fallback: keep non-front/non-back long sections if no explicit body start was found.
    fallback = []
    for item, heading, text in sections:
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        if _epub_is_front_matter(item, soup, text) or _epub_is_back_matter(item, soup, text):
            continue
        if len(text) > 500:
            fallback.append(text)
    return fallback or [text for _item, _heading, text in sections if text]



def _strip_front_matter_text(text: str) -> str:
    """Final guard for conversions that leak TOC/maps/preface text into body output."""
    starts = [
        r'^#\s+chapter\s+\d+\b',
        r'^#\s+[ivxlcdm]+\s*$',
        r'^#\s+part\s+(?:one|two|three|four|five|\d+)\b',
        r'^chapter\s+\d+\b',
    ]
    for pattern in starts:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return text[match.start():].lstrip()
    return text

def _epub_strip_terminal_chrome(text: str) -> str:
    tail = re.search(r'\n\nFollow\s+[^\n]{2,80}\s+here\s*$', text, re.IGNORECASE)
    if tail:
        return text[:tail.start()].rstrip()
    return text


def _epub_to_text(path: str) -> str:
    """Extract body-only markdown-ish text from EPUB using spine order."""
    book = epub.read_epub(path)
    sections = []
    for item in _epub_spine_items(book):
        heading, text = _epub_html_to_markdown(item)
        if text:
            sections.append((item, heading, text))
    body_sections = _epub_trim_front_and_back(sections)
    text = '\n\n'.join(body_sections)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = _strip_front_matter_text(text)
    text = _epub_strip_terminal_chrome(text)
    return text.strip()



_PDF_FRONT_MATTER_RE = re.compile(
    r'\b(?:contents|list\s+of\s+(?:maps|illustrations|figures|plates)|maps\s+and\s+illustrations|'
    r'maps|illustrations|copyright|isbn|published\s+by|all\s+rights\s+reserved|'
    r'digitized\s+by|internet\s+archive|by\s+the\s+same\s+author|preface|foreword|introduction)\b',
    re.IGNORECASE,
)

_PDF_BACK_MATTER_RE = re.compile(
    r'\b(?:acknowledgements?|acknowledgments?|select\s+bibliography|bibliography|notes|index|'
    r'about\s+the\s+author|picture\s+credits?|illustration\s+credits?)\b',
    re.IGNORECASE,
)

_PDF_BODY_START_RE = re.compile(
    r'\b(?:chapter\s+\d+|chapter\s+[ivxlcdm]+|part\s+(?:one|two|three|four|five|\d+)|'
    r'book\s+(?:one|two|three|four|five|\d+)|the\s+phony\s+war\s+that\s+wasn[’\']?t)\b',
    re.IGNORECASE,
)

_PDF_RUNNING_HEADER_RE = re.compile(
    r'^\s*(?:\d+\s+)?(?:The\s+Battle\s+of\s+the\s+Atlantic|[A-Z][A-Za-z’\'\s]{3,60})\s+(?:\d+|[ivxlcdm]+)?\s*$',
    re.IGNORECASE,
)


def _pdf_extract_pages(path: str) -> list[str]:
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text(x_tolerance=1, y_tolerance=3) or ''
            pages.append(text)
    return pages


def _pdf_symbol_garbage_score(text: str) -> float:
    compact = re.sub(r'\s+', '', text)
    if not compact:
        return 1.0
    weird = sum(1 for c in compact if not (c.isalnum() or c in '.,;:!?()[]{}\'"’“”-–—/&%£$€+-'))
    short_lines = [line.strip() for line in text.splitlines() if line.strip()]
    one_char = sum(1 for line in short_lines if len(line) <= 2)
    return (weird / max(len(compact), 1)) + (one_char / max(len(short_lines), 1))


def _pdf_is_front_matter(text: str, page_index: int) -> bool:
    sample = text[:1800]
    if page_index < 40 and _PDF_FRONT_MATTER_RE.search(sample):
        return True
    if page_index < 40 and _pdf_symbol_garbage_score(text) > 0.45:
        return True
    if page_index < 10:
        return True
    return False


def _pdf_is_back_matter(text: str) -> bool:
    for line in text.splitlines()[:12]:
        stripped = line.strip().strip(' .:-').lower()
        if not stripped:
            continue
        if re.match(r'^(?:\d+\s+)?the\s+battle\s+of\s+the\s+atlantic$', stripped):
            continue
        if re.match(r'^(?:acknowledgements?|acknowledgments?|select\s+bibliography|bibliography|notes|index|about\s+the\s+author|picture\s+credits?|illustration\s+credits?)$', stripped):
            return True
    return False


def _pdf_is_plate_page(text: str) -> bool:
    sample = '\n'.join(line.strip() for line in text.splitlines()[:20] if line.strip())
    caption_markers = len(re.findall(r'(?m)^\s*\d+\.\s+', sample))
    credit_marker = re.search(r'©|IWM|Getty|Mary\s+Evans|Collection|Press\s+Association|akg-images|Ullstein|Everett', sample, re.IGNORECASE)
    top_bottom = re.search(r'\b(?:top|bottom|above|below)\b', sample, re.IGNORECASE)
    if caption_markers >= 2 and (credit_marker or top_bottom):
        return True
    if caption_markers >= 1 and credit_marker and len(text) < 1800:
        return True
    return False


def _pdf_is_body_start(text: str) -> bool:
    if _PDF_FRONT_MATTER_RE.search(text[:1600]):
        return False
    if _pdf_symbol_garbage_score(text) > 0.45:
        return False
    if _PDF_BODY_START_RE.search(text[:1800]):
        return True
    # Fallback: a dense prose page after front matter, not a list/map/caption page.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    long_lines = [line for line in lines if len(line) > 55]
    return len(long_lines) >= 12 and len(text) > 1800


def _pdf_strip_page_chrome(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    cleaned = []
    for i, line in enumerate(lines):
        if not line:
            cleaned.append('')
            continue
        if i < 3 and _PDF_RUNNING_HEADER_RE.match(line):
            continue
        if i > len(lines) - 4 and re.match(r'^(?:\d+|[ivxlcdm]+)$', line, re.IGNORECASE):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def _pdf_repair_paragraphs(text: str) -> str:
    text = text.replace('\x0c', '\n')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'-\n(?=[a-z])', '', text)
    lines = [line.strip() for line in text.splitlines()]
    paragraphs = []
    current = []

    def flush():
        nonlocal current
        if current:
            para = ' '.join(current)
            para = re.sub(r'\s+([,.;:!?])', r'\1', para)
            para = re.sub(r'\s+', ' ', para).strip()
            if para:
                paragraphs.append(para)
            current = []

    for line in lines:
        if not line:
            flush()
            continue
        if _PDF_RUNNING_HEADER_RE.match(line) and len(line) < 80:
            continue
        if re.match(r'^(?:chapter\s+)?(?:\d+|[ivxlcdm]+)$', line, re.IGNORECASE):
            flush()
            paragraphs.append(f'# {line}')
            continue
        if re.match(r'^\d+\.\s+\S+', line):
            flush()
            paragraphs.append(f'# {line}')
            continue
        current.append(line)
    flush()
    return '\n\n'.join(paragraphs)


def _pdf_to_text(path: str) -> str:
    """Extract main-body prose from a PDF while skipping front/back matter and map OCR debris."""
    pages = _pdf_extract_pages(path)
    selected = []
    body_started = False

    for index, page_text in enumerate(pages):
        if not page_text.strip():
            continue
        if body_started and _pdf_is_back_matter(page_text):
            break
        if not body_started:
            if _pdf_is_front_matter(page_text, index):
                continue
            if not _pdf_is_body_start(page_text):
                continue
            body_started = True
        if _pdf_symbol_garbage_score(page_text) > 0.55:
            continue
        if _pdf_is_plate_page(page_text):
            continue
        selected.append(_pdf_strip_page_chrome(page_text))

    if not selected:
        selected = [_pdf_strip_page_chrome(page) for page in pages if page.strip()]

    return _pdf_repair_paragraphs('\n\n'.join(selected)).strip()

def _calibre_to_text(path: str) -> str:
    """Convert ebook formats to text via Calibre's ebook-convert CLI."""
    with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        result = subprocess.run(
            ['ebook-convert', path, tmp_path, '--txt-output-formatting=markdown'],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
        with open(tmp_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _djvu_to_text(path: str) -> str:
    """Extract text from DjVu via djvutxt."""
    result = subprocess.run(
        ['djvutxt', path],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


async def _crawl_recursive(seed_url: str, depth: int) -> str:
    """BFS crawl up to `depth` levels, staying strictly within seed_url's domain."""
    global crawl_progress
    seed_host = urlparse(seed_url).netloc
    visited: set = set()
    results = []
    queue = [(seed_url, 1)]
    crawl_progress = {"pages_done": 0, "pages_queued": 1}

    async with AsyncWebCrawler() as crawler:
        while queue and len(visited) < MAX_PAGES:
            url, current_depth = queue.pop(0)

            normalized = url.split('#')[0].rstrip('/')
            if normalized in visited:
                continue
            visited.add(normalized)

            try:
                result = await crawler.arun(url=url)
                if not result.success:
                    markdown = f"*Could not load page: {url}*"
                else:
                    markdown = result.markdown.fit_markdown or result.markdown.raw_markdown or ""
            except Exception as e:
                markdown = f"*Error crawling {url}: {e}*"

            crawl_progress["pages_done"] += 1
            results.append(f"## {url}\n\n{markdown}")

            if current_depth < depth:
                links = re.findall(r'\[(?:[^\]]*)\]\((https?://[^)\s]+)\)', markdown)
                new_links = 0
                for link in links:
                    parsed = urlparse(link)
                    if parsed.netloc == seed_host:
                        norm_link = link.split('#')[0].rstrip('/')
                        if norm_link not in visited:
                            queue.append((link, current_depth + 1))
                            new_links += 1
                crawl_progress["pages_queued"] += new_links

    return "\n\n---\n\n".join(results)


def process_input(source, is_url=False, depth=1):
    """Returns (raw, cleaned) tuple. Both are the same error string on failure."""
    if is_url:
        # Single-page requests: try Microlink first
        if depth == 1:
            microlink_result = _fetch_with_microlink(source)
            if microlink_result:
                return microlink_result, clean(microlink_result)

        # Multi-page or Microlink failed: fall back to crawl4ai
        try:
            raw = asyncio.run(_crawl_recursive(source, depth))
        except Exception as e:
            msg = str(e)
            if "Name or service not known" in msg or "getaddrinfo" in msg:
                err = "Error: Could not reach that URL — check the address and your connection."
            elif "TimeoutError" in msg or "timed out" in msg.lower():
                err = "Error: The request timed out — the site may be slow or blocking crawlers."
            else:
                err = f"Error crawling URL: {msg}"
            return err, err
        return raw, clean(raw)

    if not os.path.exists(source):
        err = "Error: File not found"
        return err, err

    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
    EPUB_EXTENSIONS = {'.epub'}
    CALIBRE_EXTENSIONS = {'.mobi', '.azw', '.azw3', '.azw4', '.fb2', '.lrf', '.rtf', '.htmlz'}
    DJVU_EXTENSIONS = {'.djvu', '.djv'}
    ext = os.path.splitext(source)[1].lower()

    if ext in IMAGE_EXTENSIONS:
        try:
            raw = pytesseract.image_to_string(Image.open(source))
        except Exception as e:
            err = f"Error: Could not run OCR on image — {e}"
            return err, err
    elif ext == '.pdf':
        try:
            raw = _pdf_to_text(source)
        except Exception as e:
            raw = ""
        if not raw or not raw.strip():
            try:
                raw = _md.convert(source).text_content
            except Exception as e:
                raw = ""
        if not raw or not raw.strip():
            try:
                pages = convert_from_path(source, dpi=200)
                raw = "\n\n".join(pytesseract.image_to_string(page) for page in pages)
            except Exception as e:
                err = f"Error: Could not OCR PDF — {e}"
                return err, err
    elif ext in EPUB_EXTENSIONS:
        try:
            raw = _epub_to_text(source)
        except Exception as e:
            err = f"Error: Could not convert epub — {e}"
            return err, err
    elif ext in CALIBRE_EXTENSIONS:
        try:
            raw = _calibre_to_text(source)
        except Exception as e:
            err = f"Error: Could not convert ebook — {e}"
            return err, err
    elif ext in DJVU_EXTENSIONS:
        try:
            raw = _djvu_to_text(source)
        except Exception as e:
            err = f"Error: Could not convert DjVu — {e}"
            return err, err
    else:
        try:
            raw = _md.convert(source).text_content
        except Exception as e:
            err = f"Error: Could not convert file — {e}"
            return err, err

    return raw, clean(raw)
