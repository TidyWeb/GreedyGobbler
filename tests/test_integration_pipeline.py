"""End-to-end extraction and packing checks using retained local fixtures."""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path
import unittest

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from converter import extract_input
from pipeline.context import prepare_context


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".test-artifacts" / "integration-fixtures"


def _write_once(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return
    with path.open("xb") as destination:
        destination.write(content)


def _epub_bytes() -> bytes:
    container = b'''<?xml version="1.0"?>
    <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
    </container>'''
    package = b'''<?xml version="1.0" encoding="UTF-8"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="book-id">integration-fixture</dc:identifier>
        <dc:title>Integration Evidence</dc:title><dc:language>en</dc:language>
      </metadata>
      <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
      <spine><itemref idref="chapter"/></spine>
    </package>'''
    chapter = b'''<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Trial Record</title></head>
    <body><h1>Trial Record</h1>
    <p>The result suggests a possible increase, but it was <em>not</em> independently replicated.</p>
    <pre><code>threshold = 0.2\nassert threshold &gt; 0</code></pre>
    <table><tr><th>Trial</th><th>Yield</th></tr><tr><td>North</td><td>18.4 L/kg</td></tr></table>
    </body></html>'''
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/chapter.xhtml", chapter)
    return stream.getvalue()


def _scan_png_bytes() -> bytes:
    image = Image.new("RGB", (1500, 360), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 52)
    except OSError:
        font = ImageFont.load_default(size=52)
    draw.text((50, 85), "SCANNED MARKER: orchid 731", fill="black", font=font)
    draw.text((50, 170), "The reading was not independently checked.", fill="black", font=font)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def _native_pdf_bytes() -> bytes:
    stream = io.BytesIO()
    document = canvas.Canvas(stream, pagesize=letter)
    document.setFont("Helvetica", 13)
    document.drawString(54, 735, "NATIVE PDF MARKER: North trial 18.4 litres per kilogram.")
    document.drawString(54, 705, "The result suggests a possible increase, not a proven effect.")
    document.save()
    return stream.getvalue()


def _mixed_pdf_bytes(scan_path: Path) -> bytes:
    stream = io.BytesIO()
    document = canvas.Canvas(stream, pagesize=letter)
    document.setFont("Helvetica", 13)
    document.drawString(54, 735, "MIXED PDF NATIVE PAGE: baseline measurement 12.6 units.")
    document.showPage()
    document.drawImage(ImageReader(str(scan_path)), 45, 515, width=520, height=125)
    document.showPage()
    document.save()
    return stream.getvalue()


def _ocr_prerequisites_available() -> bool:
    if not (shutil.which("tesseract") and shutil.which("pdftoppm")):
        return False
    try:
        ImageFont.truetype("DejaVuSans.ttf", 12)
        return True
    except OSError:
        return False


def ensure_integration_fixtures() -> dict[str, Path]:
    text = ARTIFACTS / "field-note.txt"
    html = ARTIFACTS / "technical-note.html"
    epub = ARTIFACTS / "technical-note.epub"
    native_pdf = ARTIFACTS / "native-text.pdf"
    scan_png = ARTIFACTS / "scan-page.png"
    mixed_pdf = ARTIFACTS / "mixed-scan-text.pdf"

    _write_once(
        text,
        (
            "Trial North recorded a yield of 18.4 litres per kilogram. "
            "The result suggests a possible increase, but it was not independently replicated.\n\n"
            "```python\nthreshold = 0.2\nassert threshold > 0\n```\n\n"
            "| Trial | Yield |\n| --- | ---: |\n| North | 18.4 L/kg |\n"
        ).encode("utf-8"),
    )
    _write_once(
        html,
        b'''<!doctype html><html><head><title>Technical note</title></head><body>
        <main><h1>Trial results</h1><p>The result suggests a possible increase, but it was not independently replicated.</p>
        <pre><code>threshold = 0.2\nassert threshold &gt; 0</code></pre>
        <table><tr><th>Trial</th><th>Yield</th></tr><tr><td>North</td><td>18.4 L/kg</td></tr></table></main>
        </body></html>''',
    )
    _write_once(epub, _epub_bytes())
    _write_once(native_pdf, _native_pdf_bytes())
    _write_once(scan_png, _scan_png_bytes())
    _write_once(mixed_pdf, _mixed_pdf_bytes(scan_png))
    return {
        "text": text,
        "html": html,
        "epub": epub,
        "native_pdf": native_pdf,
        "scan_png": scan_png,
        "mixed_pdf": mixed_pdf,
    }


class ExtractionContextIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = ensure_integration_fixtures()

    def test_real_formats_flow_through_extractor_and_all_packing_modes(self):
        expectations = {
            "text": ("18.4 litres per kilogram", "not independently replicated", "not independently replicated"),
            "html": ("18.4 L/kg", "threshold = 0.2", "not independently replicated"),
            "epub": ("18.4 L/kg", "threshold = 0.2", "*not* independently replicated"),
            "native_pdf": ("18.4 litres per kilogram", "suggests a possible increase", "not a proven effect"),
        }
        for kind, phrases in expectations.items():
            with self.subTest(kind=kind):
                extraction = extract_input(str(self.fixtures[kind]))
                self.assertEqual(extraction["sources"][0]["uri"], self.fixtures[kind].name)
                self.assertEqual(extraction["units"][0]["id"], "S1-U0001")
                self.assertEqual(extraction["units"][0]["source_id"], "S1")
                for phrase in phrases[:2]:
                    self.assertIn(phrase, extraction["raw"])
                    self.assertIn(phrase, extraction["cleaned"])

                for mode in ("faithful", "balanced", "focused"):
                    packed = prepare_context(
                        extraction,
                        {
                            "tokenizer": "utf8_bytes",
                            "max_tokens": 20000,
                            "mode": mode,
                            "query": "yield not independently replicated",
                        },
                    )
                    self.assertLessEqual(packed["stats"]["output_tokens"], 20000)
                    self.assertEqual(packed["stats"]["count_kind"], "utf8_bytes_bound")
                    self.assertTrue(packed["selected"])
                    self.assertTrue(all(item.get("source_id") and item.get("unit_id") for item in packed["selected"]))
                    self.assertIn(phrases[2], packed["content"])

    @unittest.skipUnless(
        _ocr_prerequisites_available(),
        "real mixed-page OCR skipped; install Tesseract, Poppler, and DejaVu Sans",
    )
    def test_mixed_scanned_and_native_pdf_pages_are_bounded_ordered_units(self):
        extraction = extract_input(str(self.fixtures["mixed_pdf"]))
        self.assertEqual(extraction["metrics"]["per_source"][0]["page_count"], 2)
        self.assertEqual(extraction["metrics"]["per_source"][0]["ocr_pages"], [2])
        self.assertEqual([unit["locator"] for unit in extraction["units"]], ["page 1", "page 2"])
        self.assertIn("MIXED PDF NATIVE PAGE", extraction["units"][0]["raw"])
        self.assertIn("SCANNED MARKER", extraction["units"][1]["raw"])
        self.assertIn("not independently checked", extraction["units"][1]["raw"])
        self.assertFalse(extraction["omissions"])

        packed = prepare_context(
            extraction,
            {"tokenizer": "utf8_bytes", "max_tokens": 5000, "mode": "faithful"},
        )
        self.assertLessEqual(packed["stats"]["output_tokens"], 5000)
        self.assertIn("SCANNED MARKER", packed["content"])
        self.assertIn("MIXED PDF NATIVE PAGE", packed["content"])


if __name__ == "__main__":
    unittest.main()
