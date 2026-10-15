"""Faithful extraction of local documents into ordered source units."""

from __future__ import annotations

import os
import posixpath
import re
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from pipeline.normaliser import clean


class ConversionError(ValueError):
    """An actionable error while reading or converting a source."""


_TABLE_CELL_RE = re.compile(r"\s+")


def _symbol_garbage_score(text: str) -> float:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return 1.0
    allowed = set(".,;:!?()[]{}'\"’“”-–—/&%£$€+-_*=<>|@#~`\\")
    strange = sum(1 for char in compact if not char.isalnum() and char not in allowed)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    one_char = sum(1 for line in lines if len(line) <= 2)
    return (strange / max(len(compact), 1)) + (one_char / max(len(lines), 1))


def _default_ocr_pdf_page(path: str, page_number: int) -> str:
    try:
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise RuntimeError("PDF page OCR needs pdf2image and Poppler installed") from exc
    try:
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("PDF page OCR needs pytesseract and Tesseract installed") from exc

    images = convert_from_path(
        path,
        dpi=200,
        first_page=page_number,
        last_page=page_number,
        thread_count=1,
        timeout=90,
    )
    if not images:
        return ""
    image = images[0]
    try:
        return pytesseract.image_to_string(image, timeout=60) or ""
    finally:
        image.close()
        for extra_image in images[1:]:
            extra_image.close()


def _table_to_markdown(table: list[list[object]]) -> str:
    rows = []
    for row in table:
        cells = [
            _TABLE_CELL_RE.sub(" ", str(cell or "").replace("\n", " ")).strip()
            .replace("|", "\\|")
            for cell in row
        ]
        if any(cells):
            rows.append(cells)
    if len(rows) < 2:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    header = rows[0]
    separator = ["---"] * width
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(lines)


def _extract_pdf_page(path: str, page_number: int, page, ocr_pdf_page, warnings: list[str], ocr_pages: list[int]) -> tuple[str, bool]:
    try:
        native_text = page.extract_text(x_tolerance=1, y_tolerance=3) or ""
        tables = []
        try:
            for table in page.extract_tables() or []:
                markdown_table = _table_to_markdown(table)
                if markdown_table:
                    tables.append(markdown_table)
        except Exception as exc:
            warnings.append(f"Page {page_number}: table structure could not be extracted ({exc}).")

        text = native_text
        needs_ocr = not native_text.strip() or _symbol_garbage_score(native_text) > 0.4
        if needs_ocr:
            try:
                ocr_text = ocr_pdf_page(path, page_number) or ""
                if ocr_text.strip():
                    if not native_text.strip() or _symbol_garbage_score(ocr_text) < _symbol_garbage_score(native_text):
                        text = ocr_text
                    ocr_pages.append(page_number)
                else:
                    warnings.append(f"Page {page_number}: OCR returned no readable text.")
            except Exception as exc:
                warnings.append(f"Page {page_number}: OCR could not run ({exc}).")

        if getattr(page, "images", None):
            warnings.append(
                f"Page {page_number}: embedded visual content was not interpreted; OCR, if used, only reports recognized text."
            )
        if not text.strip() and not tables:
            warnings.append(f"Page {page_number}: no readable text was extracted.")

        page_parts = [text.strip()] if text.strip() else []
        for table_index, table_markdown in enumerate(tables, start=1):
            page_parts.append(f"### Structured table extract {table_index} (best effort)\n\n{table_markdown}")
        if tables and text.strip():
            warnings.append(
                f"Page {page_number}: structured table text is shown as a bounded best-effort block and may repeat cells in the page text."
            )
        page_body = "\n\n".join(page_parts)
        raw = f"## Page {page_number}\n\n{page_body}" if page_body else f"## Page {page_number}"
        return raw, bool(text.strip() or tables)
    finally:
        close = getattr(page, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                warnings.append(f"Page {page_number}: layout cache could not be released ({exc}).")


def _extract_pdf(path: str, source_id: str, ocr_pdf_page: Callable[[str, int], str]) -> tuple[list[dict], list[str], dict]:
    try:
        import pdfplumber
    except ImportError as exc:
        raise ConversionError("PDF extraction needs pdfplumber installed") from exc

    units: list[dict] = []
    warnings: list[str] = []
    ocr_pages = []
    readable_pages = 0
    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for page_number, page in enumerate(pdf.pages, start=1):
                raw, readable = _extract_pdf_page(path, page_number, page, ocr_pdf_page, warnings, ocr_pages)
                readable_pages += int(readable)
                units.append({
                    "id": f"{source_id}-U{page_number:04d}",
                    "source_id": source_id,
                    "locator": f"page {page_number}",
                    "raw": raw,
                    "text": clean(raw, profile="document"),
                })
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"Could not read PDF: {exc}") from exc

    if readable_pages == 0:
        raise ConversionError("PDF extraction found no readable text. It may be an image-only scan; install OCR dependencies and try again.")

    return units, warnings, {
        "unit_count": len(units),
        "page_count": page_count,
        "ocr_pages": ocr_pages,
        "pages_with_text": readable_pages,
    }


def _epub_inline(node) -> str:
    from bs4 import NavigableString, Tag

    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node).replace("\xa0", " "))
    if not isinstance(node, Tag):
        return ""

    name = node.name.lower()
    if name == "br":
        return "\n"
    if name == "img":
        alt = (node.get("alt") or "").strip()
        src = (node.get("src") or "").strip()
        return f"![{alt}]({src})" if src else alt
    value = "".join(_epub_inline(child) for child in node.children)
    if name == "a":
        href = (node.get("href") or "").strip()
        label = value.strip() or href
        return f"[{label}]({href})" if href else label
    if name == "sup":
        return f"<sup>{value.strip()}</sup>"
    if name == "sub":
        return f"<sub>{value.strip()}</sub>"
    if name in {"strong", "b"}:
        return f"**{value.strip()}**"
    if name in {"em", "i"}:
        return f"*{value.strip()}*"
    if name == "code":
        run = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0) + 1
        fence = "`" * max(1, run)
        return f"{fence}{value.strip()}{fence}"
    return value


def _epub_pre(node) -> str:
    content = node.get_text("", strip=False).replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", content)), default=0)
    fence = "`" * max(3, longest + 1)
    language = ""
    code = node.find("code")
    if code:
        classes = code.get("class", [])
        for class_name in classes:
            if str(class_name).startswith("language-"):
                language = str(class_name)[9:]
                break
    return f"{fence}{language}\n{content}\n{fence}"


def _epub_list(node, depth: int = 0) -> str:
    from bs4 import Tag

    ordered = node.name.lower() == "ol"
    try:
        start = int(node.get("start", 1)) if ordered else 1
    except (ValueError, TypeError):
        start = 1
    lines = []
    item_number = start
    for item in node.find_all("li", recursive=False):
        text_parts = []
        nested_lists = []
        for child in item.children:
            if isinstance(child, Tag) and child.name.lower() in {"ol", "ul"}:
                nested_lists.append(_epub_list(child, depth + 1))
            else:
                text_parts.append(_epub_inline(child).strip())
        item_text = re.sub(r"\s+", " ", " ".join(part for part in text_parts if part)).strip()
        marker = f"{item_number}." if ordered else "-"
        indent = "  " * depth
        lines.append(f"{indent}{marker} {item_text}".rstrip())
        item_number += 1
        for nested in nested_lists:
            lines.extend(nested.splitlines())
    return "\n".join(lines)


def _epub_table(node) -> str:
    caption = node.find("caption", recursive=False)
    output = []
    if caption:
        caption_text = _epub_inline(caption).strip()
        if caption_text:
            output.append(f"Table: {caption_text}")
    rows = []
    for row in node.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if cells:
            rows.append([re.sub(r"\s+", " ", _epub_inline(cell)).strip().replace("|", "\\|") for cell in cells])
    if rows:
        width = max(len(row) for row in rows)
        rows = [row + [""] * (width - len(row)) for row in rows]
        output.append("| " + " | ".join(rows[0]) + " |")
        output.append("| " + " | ".join(["---"] * width) + " |")
        output.extend("| " + " | ".join(row) + " |" for row in rows[1:])
    return "\n".join(output)


def _epub_block(node) -> str:
    from bs4 import NavigableString, Tag

    if isinstance(node, NavigableString):
        value = re.sub(r"\s+", " ", str(node).replace("\xa0", " ")).strip()
        return value
    if not isinstance(node, Tag):
        return ""
    name = node.name.lower()
    if name in {"script", "style"}:
        content = node.get_text("", strip=False).strip()
        if not content:
            return ""
        fence = "```"
        while fence in content:
            fence += "`"
        return f"{fence}{name}\n{content}\n{fence}"
    if name == "pre":
        return _epub_pre(node)
    if name in {"ul", "ol"}:
        return _epub_list(node)
    if name == "table":
        return _epub_table(node)
    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = int(name[1])
        text = _epub_inline(node).strip()
        return f"{'#' * level} {text}" if text else ""
    if name == "blockquote":
        content = _epub_children(node)
        return "\n".join(f"> {line}" if line else ">" for line in content.splitlines())
    if name in {"p", "figcaption", "caption", "dt", "dd"}:
        return _epub_inline(node).strip()
    if name in {"html", "body", "main", "article", "section", "div", "aside", "nav", "figure", "header", "footer"}:
        return _epub_children(node)
    return _epub_inline(node).strip()


def _epub_children(node) -> str:
    blocks = []
    for child in node.children:
        block = _epub_block(child).strip("\n")
        if block:
            blocks.append(block)
    return "\n\n".join(blocks)


def _extract_epub(path: str, source_id: str, profile: str) -> tuple[list[dict], list[str], dict]:
    try:
        import ebooklib
        from ebooklib import epub
    except ImportError as exc:
        raise ConversionError("EPUB extraction needs EbookLib installed") from exc
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise ConversionError("EPUB extraction needs BeautifulSoup installed") from exc

    archive = None
    try:
        book = epub.read_epub(path)
        archive = zipfile.ZipFile(path)
        container_root = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next((node for node in container_root.iter() if node.tag.rsplit("}", 1)[-1] == "rootfile"), None)
        package_path = rootfile.get("full-path", "") if rootfile is not None else ""
        package_directory = posixpath.dirname(package_path)
        by_id = {item.get_id(): item for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
        spine_items = []
        for entry in getattr(book, "spine", []) or []:
            item_id = entry[0] if isinstance(entry, tuple) else entry
            if item_id in by_id:
                spine_items.append(by_id[item_id])
        if not spine_items:
            spine_items = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))
        units = []
        for spine_number, item in enumerate(spine_items, start=1):
            file_name = getattr(item, "file_name", "") or f"spine item {spine_number}"
            try:
                document_path = posixpath.normpath(posixpath.join(package_directory, file_name))
                document_html = archive.read(document_path)
            except KeyError:
                # EbookLib may normalize item paths; use its parsed content as a fallback.
                document_html = item.get_content()
            soup = BeautifulSoup(document_html, "html.parser")
            body = soup.body or soup
            raw_parts = []
            if soup.head:
                raw_parts.extend(
                    rendered
                    for tag in soup.head.find_all(["script", "style"])
                    if (rendered := _epub_block(tag).strip())
                )
            body_raw = _epub_children(body).strip()
            if body_raw:
                raw_parts.append(body_raw)
            raw_text = "\n\n".join(raw_parts).strip()
            heading_node = soup.find(["h1", "h2", "h3", "title"])
            heading = _epub_inline(heading_node).strip() if heading_node else ""
            if heading and not re.search(rf"(?m)^#{{1,6}}\s+{re.escape(heading)}\s*$", raw_text):
                raw_text = f"# {heading}\n\n{raw_text}" if raw_text else f"# {heading}"

            cleaned_soup = BeautifulSoup(document_html, "html.parser")
            for tag in cleaned_soup.find_all(["nav", "script", "style"]):
                tag.decompose()
            cleaned_body = cleaned_soup.body or cleaned_soup
            cleaned_text = _epub_children(cleaned_body).strip()
            cleaned_heading = cleaned_soup.find(["h1", "h2", "h3", "title"])
            if cleaned_heading:
                heading_text = _epub_inline(cleaned_heading).strip()
                if heading_text and not re.search(rf"(?m)^#{{1,6}}\s+{re.escape(heading_text)}\s*$", cleaned_text):
                    cleaned_text = f"# {heading_text}\n\n{cleaned_text}" if cleaned_text else f"# {heading_text}"

            if raw_text:
                units.append({
                    "id": f"{source_id}-U{len(units) + 1:04d}",
                    "source_id": source_id,
                    "locator": str(file_name),
                    "raw": raw_text,
                    "text": clean(cleaned_text, profile=profile),
                })
        archive.close()
    except ConversionError:
        if archive is not None:
            archive.close()
        raise
    except Exception as exc:
        if archive is not None:
            archive.close()
        raise ConversionError(f"Could not convert EPUB: {exc}") from exc

    if not units:
        raise ConversionError("EPUB contains no readable spine documents")
    return units, [], {"unit_count": len(units), "spine_items": len(spine_items)}


def _extract_html(path: str, source_id: str, profile: str) -> tuple[list[dict], list[str], dict]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:
        raise ConversionError("HTML extraction needs BeautifulSoup installed") from exc
    try:
        html = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ConversionError(f"Could not read HTML file: {exc}") from exc

    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup
    raw_parts = []
    if soup.head:
        raw_parts.extend(
            rendered
            for tag in soup.head.find_all(["script", "style"])
            if (rendered := _epub_block(tag).strip())
        )
    body_raw = _epub_children(body).strip()
    if body_raw:
        raw_parts.append(body_raw)
    raw_text = "\n\n".join(raw_parts).strip()

    title_node = soup.title
    title = _epub_inline(title_node).strip() if title_node else ""
    if title and not soup.find(["h1", "h2"]):
        raw_text = f"# {title}\n\n{raw_text}" if raw_text else f"# {title}"

    cleaned_soup = BeautifulSoup(html, "html.parser")
    for tag in cleaned_soup.find_all(["nav", "script", "style"]):
        tag.decompose()
    cleaned_body = cleaned_soup.body or cleaned_soup
    cleaned_text = _epub_children(cleaned_body).strip()
    cleaned_title_node = cleaned_soup.title
    cleaned_title = _epub_inline(cleaned_title_node).strip() if cleaned_title_node else ""
    if cleaned_title and not cleaned_soup.find(["h1", "h2"]):
        cleaned_text = f"# {cleaned_title}\n\n{cleaned_text}" if cleaned_text else f"# {cleaned_title}"

    if not raw_text.strip():
        raise ConversionError("HTML file contains no readable text")
    unit = {
        "id": f"{source_id}-U0001",
        "source_id": source_id,
        "locator": "document",
        "raw": raw_text,
        "text": clean(cleaned_text, profile=profile),
    }
    return [unit], [], {"unit_count": 1, "character_count": len(raw_text)}


def _read_other_file(path: str) -> str:
    extension = Path(path).suffix.lower()
    if extension in {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml"}:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ConversionError(f"Could not read file: {exc}") from exc
    if extension == ".rtf":
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError as exc:
            raise ConversionError("RTF extraction needs striprtf installed") from exc
        try:
            return rtf_to_text(Path(path).read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:
            raise ConversionError(f"Could not convert RTF: {exc}") from exc
    if extension in {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"}:
        try:
            from PIL import Image
            import pytesseract
        except ImportError as exc:
            raise ConversionError("Image OCR needs Pillow and pytesseract installed") from exc
        try:
            with Image.open(path) as image:
                return pytesseract.image_to_string(image, timeout=60) or ""
        except Exception as exc:
            raise ConversionError(f"Could not OCR image: {exc}") from exc
    if extension in {".mobi", ".azw", ".azw3", ".azw4", ".fb2", ".lrf", ".rtf", ".htmlz"}:
        try:
            fd, output_path = tempfile.mkstemp(prefix="greedygobbler-", suffix=".txt")
            os.close(fd)
            result = subprocess.run(
                ["ebook-convert", path, output_path, "--txt-output-formatting=markdown"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                detail = result.stderr.strip() or "ebook-convert returned an error"
                raise ConversionError(f"Could not convert ebook: {detail}")
            return Path(output_path).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError as exc:
            raise ConversionError("Ebook conversion needs Calibre's ebook-convert command") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("Ebook conversion timed out after 60 seconds") from exc
    if extension in {".djvu", ".djv"}:
        try:
            result = subprocess.run(["djvutxt", path], capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                raise ConversionError(f"Could not convert DjVu: {result.stderr.strip() or 'djvutxt returned an error'}")
            return result.stdout
        except FileNotFoundError as exc:
            raise ConversionError("DjVu conversion needs the djvutxt command") from exc
        except subprocess.TimeoutExpired as exc:
            raise ConversionError("DjVu conversion timed out after 60 seconds") from exc

    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise ConversionError(
            f"No local converter is installed for {extension or 'this file type'}; install MarkItDown or use PDF, EPUB, text, Markdown, or CSV."
        ) from exc
    try:
        return MarkItDown().convert(path).text_content or ""
    except Exception as exc:
        raise ConversionError(f"Could not convert file: {exc}") from exc


def extract_local(
    path: str,
    source_id: str,
    *,
    profile: str = "document",
    ocr_pdf_page: Callable[[str, int], str] | None = None,
) -> tuple[list[dict], list[str], dict]:
    """Read one local file and return source units, warnings, and metrics."""
    if not os.path.isfile(path):
        raise ConversionError("File not found")
    if not os.access(path, os.R_OK):
        raise ConversionError("File is not readable")
    extension = Path(path).suffix.lower()

    if extension == ".odt":
        raise ConversionError("OpenDocument text files (.odt) are deliberately unsupported")

    if extension == ".pdf":
        units, warnings, metrics = _extract_pdf(path, source_id, ocr_pdf_page or _default_ocr_pdf_page)
        for unit in units:
            unit["text"] = clean(unit["raw"], profile=profile)
        return units, warnings, metrics
    if extension == ".epub":
        return _extract_epub(path, source_id, profile)
    if extension in {".html", ".htm"}:
        return _extract_html(path, source_id, profile)

    raw = _read_other_file(path)
    if not raw.strip():
        raise ConversionError("The file contains no readable text")
    unit = {
        "id": f"{source_id}-U0001",
        "source_id": source_id,
        "locator": "document",
        "raw": raw,
        "text": clean(raw, profile=profile),
    }
    return [unit], [], {"unit_count": 1, "character_count": len(raw)}
