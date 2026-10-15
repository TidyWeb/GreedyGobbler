"""Opt-in checks for browser crawling and a caller-provided local compressor."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest

from converter import extract_input
from pipeline.context import prepare_context


ROOT = Path(__file__).resolve().parents[1]
BROWSER_URL = os.environ.get("GREEDYGOBBLER_TEST_BROWSER_URL", "")
BROWSER_MARKER = os.environ.get("GREEDYGOBBLER_TEST_BROWSER_MARKER", "")
COMPRESSOR_MODEL = os.environ.get("GREEDYGOBBLER_TEST_MODEL", "")


@unittest.skipUnless(
    BROWSER_URL and BROWSER_MARKER and importlib.util.find_spec("crawl4ai"),
    "optional browser path skipped; set a JavaScript-only GREEDYGOBBLER_TEST_BROWSER_URL and GREEDYGOBBLER_TEST_BROWSER_MARKER, then install requirements-browser.txt",
)
class OptionalBrowserIntegrationTests(unittest.TestCase):
    def test_explicit_browser_url_uses_the_optional_engine(self):
        result = extract_input(
            BROWSER_URL,
            is_url=True,
            profile="web",
            use_browser=True,
            allow_remote=False,
        )
        self.assertTrue(result["units"])
        self.assertIn(BROWSER_MARKER, result["raw"])


@unittest.skipUnless(
    COMPRESSOR_MODEL and Path(COMPRESSOR_MODEL).is_dir() and importlib.util.find_spec("llmlingua"),
    "optional learned compression skipped; set GREEDYGOBBLER_TEST_MODEL to an existing local model and install requirements-compression.txt",
)
class OptionalCompressionIntegrationTests(unittest.TestCase):
    def test_explicit_local_model_runs_through_context_api(self):
        source = "The pilot vessel reached stable operating conditions after careful observation of the feedstock and the surrounding process."
        extraction = {
            "raw": source,
            "cleaned": source,
            "sources": [{"id": "S1", "name": "fixture.txt", "uri": "fixture.txt"}],
            "units": [
                {
                    "id": "S1-U0001",
                    "source_id": "S1",
                    "locator": "document",
                    "raw": source,
                    "text": source,
                }
            ],
            "warnings": [],
            "omissions": [],
            "metrics": {},
        }
        result = prepare_context(
            extraction,
            {
                "tokenizer": "cl100k_base",
                "max_tokens": 1200,
                "compression": "llmlingua2",
                "compressor_model": COMPRESSOR_MODEL,
                "compression_rate": 0.5,
            },
        )
        self.assertLessEqual(result["stats"]["output_tokens"], 1200)
        self.assertTrue(result["selected"])
        self.assertTrue(any("experimental and lossy" in warning for warning in result["warnings"]))


if __name__ == "__main__":
    unittest.main()
