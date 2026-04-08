import os
import requests
from markitdown import MarkItDown
from markdownify import markdownify
from pipeline.normaliser import clean
import pytesseract
from PIL import Image

_md = MarkItDown()

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DirtyGobbler/1.0)"
}


def _fetch_url(url: str) -> str:
    """Fetch a single page and return its content as Markdown."""
    try:
        response = requests.get(url, headers=_HEADERS, timeout=15)
        response.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise Exception("Could not reach that URL — check the address and your connection.")
    except requests.exceptions.Timeout:
        raise Exception("The request timed out — the site may be slow or blocking requests.")
    except requests.exceptions.HTTPError as e:
        raise Exception(f"HTTP error: {e}")

    return markdownify(response.text, heading_style="ATX")


def process_input(source, is_url=False, depth=1):
    """Returns (raw, cleaned) tuple. Both are the same error string on failure."""
    if is_url:
        try:
            raw = _fetch_url(source)
        except Exception as e:
            err = f"Error: {e}"
            return err, err
        return raw, clean(raw)

    if not os.path.exists(source):
        err = "Error: File not found"
        return err, err

    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png'}
    ext = os.path.splitext(source)[1].lower()

    if ext == '.rtf':
        try:
            from striprtf.striprtf import rtf_to_text
            with open(source, encoding='utf-8', errors='ignore') as f:
                raw = rtf_to_text(f.read())
        except Exception as e:
            err = f"Error: Could not convert RTF — {e}"
            return err, err
    elif ext in IMAGE_EXTENSIONS:
        try:
            raw = pytesseract.image_to_string(Image.open(source))
        except Exception as e:
            err = f"Error: Could not run OCR on image — {e}"
            return err, err
    else:
        try:
            raw = _md.convert(source).text_content
        except Exception as e:
            err = f"Error: Could not convert file — {e}"
            return err, err

    return raw, clean(raw)
