"""Regression tests for preserving evidence during document extraction."""

from __future__ import annotations

import io
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".test-artifacts"
ARTIFACTS.mkdir(exist_ok=True)


import converter  # noqa: E402
from pipeline.normaliser import clean  # noqa: E402


def _write_long_pdf(path: Path, page_count: int = 11) -> None:
    pdf = canvas.Canvas(str(path))
    for page_number in range(1, page_count + 1):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 740, f"PAGE_MARKER_{page_number:02d}")
        pdf.drawString(72, 710, f"Chapter {page_number} evidence remains in the source page.")
        pdf.showPage()
    pdf.save()


def _write_mixed_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, 740, "NATIVE_PAGE_MARKER")
    pdf.drawString(72, 710, "This page contains selectable native text.")
    pdf.showPage()

    image = Image.new("RGB", (800, 300), "white")
    image_stream = io.BytesIO()
    image.save(image_stream, format="PNG")
    image_stream.seek(0)
    pdf.drawImage(ImageReader(image_stream), 72, 600, width=400, height=150)
    pdf.showPage()
    pdf.save()


def _write_front_matter_pdf(path: Path, chapter_first: bool = False) -> None:
    pdf = canvas.Canvas(str(path))
    page_texts = (
        ["Chapter 1: Evidence begins here.", "Evidence continues on the second page."]
        if chapter_first
        else [
            "TITLE PAGE AND COPYRIGHT NOTICE",
            "Preface material retained for faithful extraction.",
            "Chapter 1: The body begins here.",
            "The body continues with measured evidence.",
        ]
    )
    for page_number, text in enumerate(page_texts, start=1):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 740, f"TRIM_PAGE_{page_number}")
        pdf.drawString(72, 710, text)
        pdf.showPage()
    pdf.save()


def _write_table_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 770, "PROSE_BEFORE_TABLE remains in context.")
    x_positions = [72, 210, 350]
    y_positions = [740, 710, 680]
    for y_position in y_positions:
        pdf.line(x_positions[0], y_position, x_positions[-1], y_position)
    for x_position in x_positions:
        pdf.line(x_position, y_positions[0], x_position, y_positions[-1])
    for values, y_position in ((('Metric', 'Value'), 721), (('SAMPLE_SIZE', '128'), 691)):
        pdf.drawString(80, y_position, values[0])
        pdf.drawString(220, y_position, values[1])
    pdf.drawString(72, 650, "PROSE_AFTER_TABLE also remains in context.")
    pdf.showPage()
    pdf.save()


def _write_technical_epub(path: Path) -> None:
    container = """<?xml version="1.0"?>
    <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
    </container>"""
    package = """<?xml version="1.0" encoding="UTF-8"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="book-id">extraction-regression</dc:identifier>
        <dc:title>Extraction Regression</dc:title><dc:language>en</dc:language>
      </metadata>
      <manifest>
        <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
        <item id="after" href="after.xhtml" media-type="application/xhtml+xml"/>
      </manifest>
      <spine><itemref idref="after"/><itemref idref="chapter"/></spine>
    </package>"""
    chapter = """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head>
      <title>Chapter 1: Evidence</title><style>STYLE_EVIDENCE { color: green; }</style>
      <script>const SCRIPT_EVIDENCE = true;</script></head><body>
      <nav><p>NAV_EVIDENCE</p></nav>
      <h1>Chapter 1: Evidence</h1>
      <p>Gravity is <em>not</em> a reason to erase source meaning<sup id="note-ref"><a href="#note">1</a></sup>, and a technical exponent is x<sup>2</sup>.</p>
      <aside epub:type="footnote" id="note"><p>FOOTNOTE_EVIDENCE remains attached.</p></aside>
      <pre><code>def preserve_evidence():
    return "CODE_EVIDENCE"</code></pre>
      <table><caption>TABLE_CAPTION_EVIDENCE</caption><tr><th>Heading</th><th>Result</th></tr>
      <tr><td>Trial</td><td>TABLE_CELL_EVIDENCE</td></tr></table>
      <ol><li>OUTER_ORDERED_ITEM<ul><li>NESTED_UNORDERED_ITEM</li></ul></li></ol>
      <p><a href="https://example.invalid/source#part">SOURCE_LINK_LABEL</a></p>
    </body></html>"""
    after = """<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>Later spine item</h1>
      <p>SPINE_FIRST_IN_DECLARED_ORDER</p></body></html>"""
    with zipfile.ZipFile(path, "w") as epub_file:
        epub_file.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        epub_file.writestr("META-INF/container.xml", container)
        epub_file.writestr("OEBPS/content.opf", package)
        epub_file.writestr("OEBPS/chapter.xhtml", chapter)
        epub_file.writestr("OEBPS/after.xhtml", after)


class CleanerEvidenceRegressionTests(unittest.TestCase):
    def test_document_profile_does_not_remove_prose_that_mentions_ui_phrases(self):
        source = (
            "## Source: usability study\n\n"
            "The participant said, ‘accept all cookies’ before the experiment began.\n\n"
            "Use cookies in this recipe to keep the cake moist."
        )
        result = clean(source)
        self.assertIn("accept all cookies", result)
        self.assertIn("Use cookies in this recipe", result)
        self.assertIn("## Source: usability study", result)

    def test_web_ui_removal_is_limited_to_marked_edge_lines(self):
        edge_block = "Accept all cookies\nCookie settings\n\n# Article\n\nThe article mentions accept all cookies in a sentence."
        result = clean(edge_block, profile="web")
        self.assertNotIn("Cookie settings", result)
        self.assertNotIn("Accept all cookies\n", result)
        self.assertIn("# Article", result)
        self.assertIn("mentions accept all cookies in a sentence", result)

        unmarked_line = clean("A researcher wrote:\n\nAccept all cookies\n\nThe study continued.", profile="web")
        self.assertIn("Accept all cookies", unmarked_line)

    def test_identical_results_under_distinct_headings_are_preserved(self):
        result = clean(
            "## Trial A\n\nThe measured result was 42.\n\n"
            "## Trial B\n\nThe measured result was 42."
        )
        self.assertEqual(result.count("The measured result was 42."), 2)

    def test_variable_fences_tilde_fences_and_indented_code_are_preserved(self):
        source = (
            "~~~~python\nprint('accept all cookies')\n~~~~\n\n"
            "````text\n```.inner marker\n````\n\n"
            "    # subscribe to our newsletter\n"
            "    print('indented evidence')"
        )
        result = clean(source)
        self.assertIn("~~~~python\nprint('accept all cookies')\n~~~~", result)
        self.assertIn("````text\n```.inner marker\n````", result)
        self.assertIn("    # subscribe to our newsletter", result)
        self.assertIn("    print('indented evidence')", result)

    def test_indented_code_at_document_edges_and_hard_breaks_keep_their_spacing(self):
        leading_code = "    result = 42\n"
        trailing_code = "Explanation.\n\n    result = 42\n"
        hard_break = "First line.  \nSecond line."
        self.assertEqual(clean(leading_code), leading_code)
        self.assertEqual(clean(trailing_code), trailing_code)
        self.assertEqual(clean(hard_break), hard_break)

    def test_references_links_figures_captions_and_alt_text_are_retained(self):
        source = (
            "## Source: Field report\n\n"
            "See [the source document][source] for the method.\n\n"
            "![Site map showing three survey locations](map.png)\n\n"
            "Figure 2. Survey locations across the study area.\n\n"
            "[source]: https://example.invalid/report"
        )
        result = clean(source)
        for evidence in (
            "## Source: Field report",
            "[the source document][source]",
            "Site map showing three survey locations",
            "Figure 2. Survey locations across the study area.",
            "[source]: https://example.invalid/report",
        ):
            self.assertIn(evidence, result)

    def test_large_blank_runs_are_normalised_without_losing_neighbours(self):
        source = "Beginning\n" + ("\n" * 4000) + "Ending"
        self.assertEqual(clean(source), "Beginning\n\nEnding")


class SourceExtractionRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.all_pages_pdf = ARTIFACTS / "all-pages.pdf"
        cls.mixed_pdf = ARTIFACTS / "mixed-native-and-image.pdf"
        cls.front_matter_pdf = ARTIFACTS / "front-matter-before-body.pdf"
        cls.chapter_first_pdf = ARTIFACTS / "chapter-first.pdf"
        cls.table_pdf = ARTIFACTS / "table-grid.pdf"
        cls.technical_epub = ARTIFACTS / "technical-blocks.epub"
        _write_long_pdf(cls.all_pages_pdf)
        _write_mixed_pdf(cls.mixed_pdf)
        _write_front_matter_pdf(cls.front_matter_pdf)
        _write_front_matter_pdf(cls.chapter_first_pdf, chapter_first=True)
        _write_table_pdf(cls.table_pdf)
        _write_technical_epub(cls.technical_epub)

    def test_pdf_raw_extraction_keeps_the_first_ten_pages(self):
        raw = converter._pdf_to_text(str(self.all_pages_pdf))
        for page_number in range(1, 12):
            with self.subTest(page=page_number):
                self.assertIn(f"PAGE_MARKER_{page_number:02d}", raw)

    def test_pdf_ocr_fills_an_image_only_page_in_a_mixed_document(self):
        original_ocr = converter._ocr_pdf_page
        observed_pages = []

        def fake_ocr(path, page_number):
            observed_pages.append(page_number)
            return "OCR_PAGE_MARKER" if page_number == 2 else ""

        converter._ocr_pdf_page = fake_ocr
        try:
            result = converter.extract_input(str(self.mixed_pdf))
        finally:
            converter._ocr_pdf_page = original_ocr
        self.assertIn("NATIVE_PAGE_MARKER", result["raw"])
        self.assertIn("OCR_PAGE_MARKER", result["raw"])
        self.assertEqual(observed_pages, [2])
        self.assertIn("page 2", result["units"][1]["locator"])

    def test_pdf_pages_release_pdfplumber_layout_cache_after_extraction(self):
        class FakePage:
            images = []

            def __init__(self):
                self.closed = False

            def extract_text(self, **_kwargs):
                return "PAGE_CACHE_EVIDENCE remains available after close."

            def extract_tables(self):
                return []

            def find_tables(self):
                return []

            def close(self):
                self.closed = True

        page = FakePage()

        class FakePdf:
            pages = [page]

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        with mock.patch("pdfplumber.open", return_value=FakePdf()):
            result = converter.extract_input(str(self.all_pages_pdf))
        self.assertTrue(page.closed)
        self.assertIn("PAGE_CACHE_EVIDENCE", result["units"][0]["raw"])
        self.assertEqual(result["metrics"]["per_source"][0]["pages_with_text"], 1)

    def test_pdf_cleaned_text_removes_detected_table_copy_and_keeps_prose(self):
        result = converter.extract_input(str(self.table_pdf))
        self.assertIn("### Structured table extract 1 (best effort)", result["raw"])
        self.assertEqual(result["raw"].count("SAMPLE_SIZE"), 2)
        self.assertEqual(result["raw"].count("128"), 2)
        self.assertIn("PROSE_BEFORE_TABLE remains in context.", result["raw"])
        self.assertIn("PROSE_AFTER_TABLE also remains in context.", result["raw"])
        self.assertIn("| SAMPLE_SIZE | 128 |", result["cleaned"])
        self.assertEqual(result["cleaned"].count("SAMPLE_SIZE"), 1)
        self.assertEqual(result["cleaned"].count("128"), 1)
        self.assertIn("PROSE_BEFORE_TABLE remains in context.", result["cleaned"])
        self.assertIn("PROSE_AFTER_TABLE also remains in context.", result["cleaned"])
        self.assertFalse(any("may repeat cells" in warning for warning in result["warnings"]))

    def test_pdf_keeps_native_content_when_table_geometry_cannot_be_verified(self):
        class FakePage:
            images = []

            def __init__(self):
                self.closed = False

            def extract_text(self, **_kwargs):
                return "SURROUNDING PROSE and SAMPLE_SIZE 128 remain native."

            def find_tables(self):
                raise RuntimeError("geometry unavailable")

            def extract_tables(self):
                return [[ ["Metric", "Value"], ["SAMPLE_SIZE", "128"] ]]

            def close(self):
                self.closed = True

        page = FakePage()

        class FakePdf:
            pages = [page]

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

        with mock.patch("pdfplumber.open", return_value=FakePdf()):
            result = converter.extract_input(str(self.all_pages_pdf))
        self.assertTrue(page.closed)
        self.assertIn("SURROUNDING PROSE and SAMPLE_SIZE 128 remain native.", result["cleaned"])
        self.assertIn("| SAMPLE_SIZE | 128 |", result["cleaned"])
        self.assertTrue(any("native text is retained" in warning for warning in result["warnings"]))
        self.assertTrue(any("cells may repeat" in warning for warning in result["warnings"]))

    def test_ocr_failure_is_reported_for_only_the_unextractable_page(self):
        original_ocr = converter._ocr_pdf_page

        def failed_ocr(_path, page_number):
            if page_number == 2:
                raise RuntimeError("Tesseract is unavailable")
            return ""

        converter._ocr_pdf_page = failed_ocr
        try:
            result = converter.extract_input(str(self.mixed_pdf))
        finally:
            converter._ocr_pdf_page = original_ocr
        self.assertIn("NATIVE_PAGE_MARKER", result["raw"])
        self.assertTrue(any("Page 2: OCR could not run" in warning for warning in result["warnings"]))
        self.assertTrue(any("Page 2: no readable text" in warning for warning in result["warnings"]))

    def test_epub_raw_extraction_retains_technical_blocks_notes_and_hierarchy(self):
        raw = converter._epub_to_text(str(self.technical_epub))
        for evidence in (
            "SPINE_FIRST_IN_DECLARED_ORDER",
            "Chapter 1: Evidence",
            "<sup>[1](#note)</sup>",
            "x<sup>2</sup>",
            "x<sup>2</sup>",
            "FOOTNOTE_EVIDENCE remains attached.",
            "CODE_EVIDENCE",
            "TABLE_CAPTION_EVIDENCE",
            "TABLE_CELL_EVIDENCE",
            "OUTER_ORDERED_ITEM",
            "NESTED_UNORDERED_ITEM",
            "SOURCE_LINK_LABEL",
            "NAV_EVIDENCE",
            "STYLE_EVIDENCE",
            "SCRIPT_EVIDENCE",
        ):
            with self.subTest(evidence=evidence):
                self.assertIn(evidence, raw)

    def test_epub_cleaned_copy_removes_navigation_scripts_and_styles_only(self):
        result = converter.extract_input(str(self.technical_epub), profile="web")
        self.assertLess(result["raw"].index("SPINE_FIRST_IN_DECLARED_ORDER"), result["raw"].index("Chapter 1: Evidence"))
        self.assertIn("FOOTNOTE_EVIDENCE remains attached.", result["cleaned"])
        self.assertIn("CODE_EVIDENCE", result["cleaned"])
        self.assertIn("TABLE_CELL_EVIDENCE", result["cleaned"])
        self.assertNotIn("NAV_EVIDENCE", result["cleaned"])
        self.assertNotIn("STYLE_EVIDENCE", result["cleaned"])
        self.assertNotIn("SCRIPT_EVIDENCE", result["cleaned"])

    def test_local_html_is_rendered_to_markdown_and_cleaned_from_a_dom_copy(self):
        html_path = ARTIFACTS / "article.html"
        html_path.write_text("""<!doctype html><html><head><title>HTML Evidence</title>
          <style>HTML_STYLE_CHROME</style><script>HTML_SCRIPT_CHROME</script></head><body>
          <nav>NAV_CHROME</nav><main><h1>HTML Evidence</h1><p>HTML_BODY_EVIDENCE</p>
          <pre><code>HTML_CODE_EVIDENCE</code></pre></main></body></html>""", encoding="utf-8")
        result = converter.extract_input(str(html_path))
        self.assertIn("HTML_BODY_EVIDENCE", result["raw"])
        self.assertIn("HTML_CODE_EVIDENCE", result["raw"])
        self.assertIn("HTML_BODY_EVIDENCE", result["cleaned"])
        for marker in ("HTML_STYLE_CHROME", "HTML_SCRIPT_CHROME", "NAV_CHROME"):
            self.assertIn(marker, result["raw"])
            self.assertNotIn(marker, result["cleaned"])

    def test_rtf_uses_standalone_text_conversion(self):
        rtf_path = ARTIFACTS / "evidence.rtf"
        rtf_path.write_text(r"{\rtf1\ansi RTF_EVIDENCE \b bold\b0 text.}", encoding="utf-8")
        result = converter.extract_input(str(rtf_path))
        self.assertIn("RTF_EVIDENCE", result["raw"])
        self.assertIn("bold", result["cleaned"])

    def test_shared_api_shape_and_legacy_tuple_are_preserved(self):
        result = converter.extract_input(str(self.all_pages_pdf))
        self.assertEqual(set(result), {"raw", "cleaned", "sources", "units", "warnings", "omissions", "metrics"})
        self.assertEqual(result["sources"], [{"id": "S1", "name": self.all_pages_pdf.name, "uri": self.all_pages_pdf.name}])
        self.assertEqual(result["units"][0]["id"], "S1-U0001")
        self.assertEqual(result["units"][-1]["locator"], "page 11")
        self.assertEqual(len(converter.process_input(str(self.all_pages_pdf))), 2)

    def test_explicit_trim_changes_cleaned_only_and_reports_omitted_units(self):
        full = converter.extract_input(str(self.front_matter_pdf))
        trimmed = converter.extract_input(str(self.front_matter_pdf), trim_body=True)
        self.assertEqual(trimmed["raw"], full["raw"])
        self.assertEqual(len(trimmed["units"]), 4)
        self.assertEqual(trimmed["units"][0]["text"], "")
        self.assertEqual(trimmed["units"][1]["text"], "")
        self.assertEqual([item["unit_id"] for item in trimmed["omissions"]], ["S1-U0001", "S1-U0002"])
        self.assertNotIn("Preface material", trimmed["cleaned"])
        self.assertIn("Chapter 1: The body begins here.", trimmed["cleaned"])

    def test_body_trim_is_scoped_to_each_input_source(self):
        result = converter.extract_input([str(self.front_matter_pdf), str(self.chapter_first_pdf)], trim_body=True)
        self.assertEqual(result["sources"][1]["id"], "S2")
        self.assertIn("TRIM_PAGE_1", result["cleaned"])
        self.assertEqual([item["source_id"] for item in result["omissions"]], ["S1", "S1"])

    def test_body_trim_does_not_treat_indented_code_as_a_chapter_heading(self):
        for code in ("    Chapter 1: example code\n", "```text\nChapter 1: example code\n```\n"):
            units = [{
                "id": "S1-U0001", "source_id": "S1", "locator": "document",
                "raw": code, "text": code,
            }]
            warnings = []
            selected, omissions = converter._body_trim(units, warnings)
            self.assertEqual(selected, units)
            self.assertEqual(omissions, [])
            self.assertTrue(any("no clear chapter" in warning for warning in warnings))

    def test_epub_cleaned_copy_removes_navigation_scripts_and_styles_only(self):
        result = converter.extract_input(str(self.technical_epub), profile="web")
        self.assertIn("NAV_EVIDENCE", result["raw"])
        self.assertIn("SCRIPT_EVIDENCE", result["raw"])
        self.assertIn("STYLE_EVIDENCE", result["raw"])
        self.assertNotIn("NAV_EVIDENCE", result["cleaned"])
        self.assertNotIn("SCRIPT_EVIDENCE", result["cleaned"])
        self.assertNotIn("STYLE_EVIDENCE", result["cleaned"])

    def test_import_does_not_load_optional_browser_or_model_engines(self):
        code = "import sys, converter; assert 'crawl4ai' not in sys.modules; assert 'markitdown' not in sys.modules"
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_empty_source_and_invalid_depth_are_actionable_errors(self):
        for source, depth in (("", 1), (str(self.all_pages_pdf), True), (str(self.all_pages_pdf), 0)):
            with self.subTest(source=source, depth=depth):
                with self.assertRaises(converter.ConversionError):
                    converter.extract_input(source, depth=depth)
        raw, cleaned = converter.process_input("/missing/source.pdf")
        self.assertTrue(raw.startswith("Error:"))
        self.assertEqual(raw, cleaned)

    def test_odt_remains_deliberately_unsupported(self):
        odt_path = ARTIFACTS / "unsupported.odt"
        odt_path.write_text("not an OpenDocument package", encoding="utf-8")
        with self.assertRaisesRegex(converter.ConversionError, "deliberately unsupported"):
            converter.extract_input(str(odt_path))


if __name__ == "__main__":
    unittest.main()
