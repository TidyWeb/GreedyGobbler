from __future__ import annotations

import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pipeline.context import prepare_context
from pipeline.context_models import ContextBlock
from pipeline.token_budget import make_counter


ROOT = Path(__file__).resolve().parents[1]


def extraction(markdown: str, *, source_id: str = "S1", unit_id: str | None = None):
    unit_id = unit_id or f"{source_id}-U0001"
    return {
        "raw": markdown,
        "cleaned": markdown,
        "sources": [
            {
                "id": source_id,
                "name": "Demonstration source",
                "uri": "/private/documents/demo.md",
            }
        ],
        "units": [
            {
                "id": unit_id,
                "source_id": source_id,
                "locator": {"page": 1},
                "raw": markdown,
                "text": markdown,
            }
        ],
        "warnings": [],
        "omissions": [],
        "metrics": {},
    }


class TokenBudgetTests(unittest.TestCase):
    def test_utf8_bytes_is_an_explicit_byte_bound(self):
        counter = make_counter("utf8_bytes")
        self.assertEqual(counter.count("café"), 5)
        self.assertEqual(counter.count_kind, "utf8_bytes_bound")

    def test_unknown_or_missing_exact_tokenizer_is_actionable_without_fetch(self):
        with self.assertRaisesRegex(ValueError, "tokenizer must be"):
            make_counter("word_estimate")

        cache = Path(tempfile.gettempdir()) / "context-token-cache-that-does-not-exist"
        with patch.dict(os.environ, {"TIKTOKEN_CACHE_DIR": str(cache)}):
            with patch("tiktoken.get_encoding") as get_encoding:
                with self.assertRaisesRegex(ValueError, "not cached locally"):
                    make_counter("cl100k_base").count("hello")
                get_encoding.assert_not_called()

    def test_exact_tokenizer_uses_verified_local_cache_when_available(self):
        url = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
        cache_dir = Path(
            os.environ.get(
                "TIKTOKEN_CACHE_DIR",
                os.environ.get(
                    "DATA_GYM_CACHE_DIR",
                    str(Path(tempfile.gettempdir()) / "data-gym-cache"),
                ),
            )
        )
        cache_path = cache_dir / hashlib.sha1(url.encode()).hexdigest()
        if not cache_path.is_file():
            self.skipTest("verified cl100k_base cache is not available locally")
        counter = make_counter("cl100k_base")
        self.assertEqual(counter.count("hello world"), 2)
        self.assertEqual(counter.count_kind, "exact_tokens")

    def test_bool_and_float_budgets_are_rejected(self):
        for bad_value in (True, 12.0):
            with self.subTest(value=bad_value):
                with self.assertRaisesRegex(ValueError, "max_tokens must be an integer"):
                    prepare_context({}, {"max_tokens": bad_value, "tokenizer": "utf8_bytes"})

    def test_budget_math_and_minimum_wrapper_are_checked(self):
        with self.assertRaisesRegex(ValueError, "context_window must exceed"):
            prepare_context(
                {},
                {
                    "context_window": 40,
                    "reserved_tokens": 20,
                    "answer_tokens": 20,
                    "tokenizer": "utf8_bytes",
                },
            )
        empty = prepare_context(
            {"raw": "", "cleaned": "", "sources": [], "units": []},
            {"tokenizer": "utf8_bytes", "max_tokens": 1000},
        )
        with self.assertRaisesRegex(ValueError, "minimum context wrapper"):
            prepare_context(
                {},
                {
                    "tokenizer": "utf8_bytes",
                    "max_tokens": empty["stats"]["output_tokens"] - 1,
                },
            )


class ContextPackingTests(unittest.TestCase):
    def test_faithful_keeps_code_nested_lists_tables_and_footnotes_whole(self):
        tick = chr(96)
        markdown = (
            "## Example\n"
            + tick * 4
            + "python\ninside = "
            + tick * 3
            + "text\n"
            + tick * 4
            + "\n\n"
            + "~~~text\nTilde fence\n~~~~~\n\n"
            + "    indented code\n"
            + "    second line\n\n"
            + "- parent\n"
            + "  - child appears once\n"
            + "    - nested child\n\n"
            + "| Measure | Value |\n"
            + "| --- | ---: |\n"
            + "| rate | 12 mg |\n\n"
            + "The result is documented[^source].\n\n"
            + "[^source]: The measurement came from page one.\n"
        )
        result = prepare_context(
            extraction(markdown),
            {"tokenizer": "utf8_bytes", "max_tokens": 10000},
        )
        content = result["content"]
        self.assertIn(tick * 4 + "python\ninside = " + tick * 3 + "text\n" + tick * 4, content)
        self.assertIn("~~~text\nTilde fence\n~~~~~", content)
        self.assertIn("    indented code\n    second line", content)
        self.assertIn("- parent\n  - child appears once\n    - nested child", content)
        self.assertEqual(content.count("child appears once"), 1)
        self.assertIn("| Measure | Value |\n| --- | ---: |\n| rate | 12 mg |", content)
        self.assertIn("[^source]: The measurement came from page one.", content)
        self.assertEqual(result["stats"]["output_tokens"], len(content.encode("utf-8")))

    def test_skipped_heading_levels_preserve_sibling_paths(self):
        markdown = (
            "## Trial Alpha\n"
            "#### Unusual deep heading\n"
            "A result under the deep heading.\n\n"
            "### Trial Alpha detail\n"
            "A result under a sibling detail heading.\n\n"
            "## Trial Beta\n"
            "A result under a sibling trial heading.\n"
        )
        result = prepare_context(
            extraction(markdown), {"tokenizer": "utf8_bytes", "max_tokens": 5000}
        )
        paths = [tuple(item["path"]) for item in result["outline"]]
        self.assertEqual(
            paths,
            [
                ("Trial Alpha",),
                ("Trial Alpha", "Unusual deep heading"),
                ("Trial Alpha", "Trial Alpha detail"),
                ("Trial Beta",),
            ],
        )

    def test_table_header_and_units_are_not_separated_from_rows(self):
        markdown = "| Distance | Speed |\n| --- | --- |\n| 4 km | 20 km/h |\n"
        result = prepare_context(
            extraction(markdown),
            {"tokenizer": "utf8_bytes", "max_tokens": 1000},
        )
        selected = [record for record in result["selected"] if record["kind"] == "table"]
        self.assertEqual(len(selected), 1)
        self.assertIn("| Distance | Speed |\n| --- | --- |\n| 4 km | 20 km/h |", result["content"])

    def test_oversized_atom_is_omitted_without_slicing(self):
        markdown = "A single indivisible paragraph. " + ("evidence " * 200)
        empty = prepare_context(
            {"raw": "", "cleaned": "", "sources": [], "units": []},
            {"tokenizer": "utf8_bytes", "max_tokens": 2000},
        )
        result = prepare_context(
            extraction(markdown),
            {
                "tokenizer": "utf8_bytes",
                "max_tokens": empty["stats"]["output_tokens"] + 100,
            },
        )
        self.assertEqual(result["selected"], [])
        self.assertEqual(len(result["omitted"]), 1)
        self.assertEqual(result["omitted"][0]["reason"], "oversize_block")
        self.assertNotIn("A single indivisible paragraph.", result["content"])

    def test_focused_mode_keeps_adjacent_exception_with_matching_fact(self):
        markdown = (ROOT / "tests/fixtures/context/nearby_qualification.md").read_text(
            encoding="utf-8"
        )
        full = prepare_context(
            extraction(markdown),
            {
                "tokenizer": "utf8_bytes",
                "max_tokens": 10000,
                "mode": "focused",
                "query": "treatment children",
            },
        )
        single = prepare_context(
            extraction("## Treatment\nTreatment is approved.\n"),
            {
                "tokenizer": "utf8_bytes",
                "max_tokens": 10000,
                "mode": "focused",
                "query": "treatment",
            },
        )
        result = prepare_context(
            extraction(markdown),
            {
                "tokenizer": "utf8_bytes",
                "max_tokens": single["stats"]["output_tokens"],
                "mode": "focused",
                "query": "treatment children",
            },
        )
        self.assertIn("Treatment is approved.", full["content"])
        self.assertIn("NOT approved for children", full["content"])
        self.assertNotIn("Treatment is approved.", result["content"])
        self.assertNotIn("NOT approved for children", result["content"])
        self.assertTrue(
            any(record["reason"] == "over_budget" for record in result["omitted"])
        )

    def test_focused_mode_requires_query_and_uses_stable_ties(self):
        with self.assertRaisesRegex(ValueError, "requires a non-empty query"):
            prepare_context({}, {"mode": "focused", "tokenizer": "utf8_bytes"})
        markdown = "## Alpha\nshared point one.\n\n## Beta\nshared point two.\n"
        options = {
            "tokenizer": "utf8_bytes",
            "max_tokens": 5000,
            "mode": "focused",
            "query": "shared point",
        }
        first = prepare_context(extraction(markdown), options)
        second = prepare_context(extraction(markdown), options)
        self.assertEqual(first, second)
        self.assertLess(
            first["content"].index("shared point one"),
            first["content"].index("shared point two"),
        )

    def test_footnote_definitions_resolve_across_units_with_local_precedence(self):
        try:
            make_counter("cl100k_base").count("local tokenizer probe")
        except ValueError:
            self.skipTest("verified cl100k_base cache is not available locally")
        sources = [{"id": "S1", "name": "Multi-page source", "uri": "doc.pdf"}]
        units = [
            {
                "id": "S1-U0001",
                "source_id": "S1",
                "locator": {"page": 1},
                "raw": "# Findings\n\nTreatment improves outcome[^caution].",
                "text": "# Findings\n\nTreatment improves outcome[^caution].",
            },
            {
                "id": "S1-U0002",
                "source_id": "S1",
                "locator": {"page": 2},
                "raw": "[^caution]: NOT approved for children.",
                "text": "[^caution]: NOT approved for children.",
            },
        ]
        extraction_data = {
            "raw": "\n\n".join(unit["raw"] for unit in units),
            "cleaned": "\n\n".join(unit["text"] for unit in units),
            "sources": sources,
            "units": units,
            "omissions": [],
        }
        results = [
            prepare_context(
                extraction_data,
                {
                    "tokenizer": "cl100k_base",
                    "max_tokens": budget,
                    "mode": "focused",
                    "query": "treatment outcome",
                },
            )
            for budget in (140, 170, 200, 250)
        ]
        for result in results:
            if "Treatment improves outcome" in result["content"]:
                self.assertIn("NOT approved for children", result["content"])
                self.assertIn("S1-U0002", result["content"])
        self.assertIn("Treatment improves outcome", results[-1]["content"])
        self.assertIn("NOT approved for children", results[-1]["content"])

        local_units = [
            dict(units[0], raw="# Findings\n\nTreatment improves outcome[^caution].\n\n[^caution]: Locally approved for adults.", text="# Findings\n\nTreatment improves outcome[^caution].\n\n[^caution]: Locally approved for adults."),
            dict(units[1], raw="[^caution]: NOT approved for children.", text="[^caution]: NOT approved for children."),
        ]
        local_data = dict(extraction_data)
        local_data["units"] = local_units
        local = prepare_context(
            local_data,
            {
                "tokenizer": "cl100k_base",
                "max_tokens": 1000,
                "mode": "focused",
                "query": "treatment outcome",
            },
        )
        self.assertIn("Locally approved for adults", local["content"])
        self.assertNotIn("NOT approved for children", local["content"])

    def test_ambiguous_source_wide_footnotes_keep_each_definition(self):
        units = [
            {
                "id": "S1-U0001",
                "source_id": "S1",
                "locator": "page 1",
                "raw": "Finding[^note].",
                "text": "Finding[^note].",
            },
            {
                "id": "S1-U0002",
                "source_id": "S1",
                "locator": "page 2",
                "raw": "[^note]: First distinct caution.",
                "text": "[^note]: First distinct caution.",
            },
            {
                "id": "S1-U0003",
                "source_id": "S1",
                "locator": "page 3",
                "raw": "[^note]: Second distinct caution.",
                "text": "[^note]: Second distinct caution.",
            },
        ]
        data = {
            "raw": "\n\n".join(unit["raw"] for unit in units),
            "cleaned": "\n\n".join(unit["text"] for unit in units),
            "sources": [{"id": "S1", "name": "Doc", "uri": "doc.md"}],
            "units": units,
        }
        result = prepare_context(
            data,
            {
                "tokenizer": "utf8_bytes",
                "max_tokens": 2000,
                "mode": "focused",
                "query": "finding",
            },
        )
        self.assertIn("First distinct caution", result["content"])
        self.assertIn("Second distinct caution", result["content"])
        self.assertTrue(any("Duplicate footnote labels" in item for item in result["warnings"]))

    def test_balanced_mode_covers_sections_and_keeps_repeated_results(self):
        markdown = (
            "## Trial Alpha\nResult was 18 mg.\n\n"
            "## Trial Beta\nResult was 18 mg.\n\n"
            "## Trial Gamma\nResult was 7 mg.\n"
        )
        result = prepare_context(
            extraction(markdown),
            {"tokenizer": "utf8_bytes", "max_tokens": 5000, "mode": "balanced"},
        )
        self.assertEqual(len(result["outline"]), 3)
        self.assertEqual(result["content"].count("Result was 18 mg."), 4)
        self.assertIn("## Overview", result["content"])
        self.assertIn("## Evidence", result["content"])
        self.assertIn('"path":["Trial Alpha"]', result["content"])
        self.assertIn('"path":["Trial Beta"]', result["content"])

    def test_source_map_contains_only_sources_cited_by_selected_blocks(self):
        data = extraction("## Only source one\namber kestrel permit.\n")
        data["sources"].append({"id": "S2", "name": "Not selected", "uri": "https://example.test/2"})
        data["units"].append(
            {
                "id": "S2-U0001",
                "source_id": "S2",
                "locator": "page 2",
                "raw": "An unrelated result.",
                "text": "An unrelated result.",
            }
        )
        result = prepare_context(
            data,
            {
                "tokenizer": "utf8_bytes",
                "max_tokens": 1000,
                "mode": "focused",
                "query": "amber kestrel",
            },
        )
        self.assertIn('"id":"S1"', result["content"])
        self.assertNotIn('"id":"S2"', result["content"])
        self.assertEqual([source["id"] for source in result["sources"]], ["S1", "S2"])

    def test_extraction_omissions_remain_visible_in_export_and_metadata(self):
        data = extraction("A surviving fact.")
        data["omissions"] = [
            {"unit_id": "S1-U0002", "reason": "unsupported source format"}
        ]
        result = prepare_context(
            data, {"tokenizer": "utf8_bytes", "max_tokens": 1000}
        )
        self.assertIn("1 extracted unit(s) unavailable", result["content"])
        self.assertIn(
            {"unit_id": "S1-U0002", "reason": "unsupported source format"},
            result["omitted"],
        )

    def test_malformed_extraction_records_are_rejected_not_silently_dropped(self):
        data = extraction("First result.")
        data["raw"] = "First result.\n\nSecond result."
        data["cleaned"] = data["raw"]
        data["units"].append("malformed second page")
        with self.assertRaisesRegex(ValueError, r"extraction.units\[1\].*mapping"):
            prepare_context(data, {"tokenizer": "utf8_bytes"})

    def test_local_absolute_uri_is_reduced_to_basename_and_input_is_unchanged(self):
        data = extraction("## Source\nA preserved fact.\n")
        before = copy.deepcopy(data)
        result = prepare_context(data, {"tokenizer": "utf8_bytes", "max_tokens": 1000})
        self.assertEqual(data, before)
        self.assertEqual(result["sources"][0]["uri"], "demo.md")
        self.assertNotIn("/private/documents", result["content"])

    def test_empty_and_non_english_documents_are_deterministic(self):
        empty = prepare_context(
            {"raw": "", "cleaned": "", "sources": [], "units": []},
            {"tokenizer": "utf8_bytes", "max_tokens": 1000},
        )
        self.assertIn("## Evidence", empty["content"])
        markdown = "## Résumé\nLe café coûte 5 € par jour. 東京の天気は晴れです。\n"
        result = prepare_context(
            extraction(markdown),
            {"tokenizer": "utf8_bytes", "max_tokens": 3000},
        )
        self.assertIn("café", result["content"])
        self.assertIn("東京", result["content"])

    def test_full_export_metadata_is_counted_and_success_always_fits(self):
        markdown = "A source-backed result."
        data = extraction(markdown)
        generous = prepare_context(
            data, {"tokenizer": "utf8_bytes", "max_tokens": 2000}
        )
        exact = prepare_context(
            data,
            {
                "tokenizer": "utf8_bytes",
                "max_tokens": generous["stats"]["output_tokens"],
            },
        )
        self.assertEqual(exact["stats"]["output_tokens"], len(exact["content"].encode()))
        self.assertLessEqual(exact["stats"]["output_tokens"], exact["stats"]["budget_tokens"])
        self.assertIn("Source map", exact["content"])
        tighter = prepare_context(
            data,
            {
                "tokenizer": "utf8_bytes",
                "max_tokens": generous["stats"]["output_tokens"] - 1,
            },
        )
        self.assertLessEqual(
            tighter["stats"]["output_tokens"], tighter["stats"]["budget_tokens"]
        )
        self.assertEqual(tighter["selected"], [])
        self.assertTrue(tighter["omitted"])

    def test_all_public_options_are_validated(self):
        invalid_options = [
            {"tokenizer": "fake"},
            {"mode": "short"},
            {"context_window": 10, "reserved_tokens": 10},
            {"reserved_tokens": True},
            {"query": 4},
            {"compression": "remote"},
            {"compression_rate": True},
            {"compression_rate": 0},
            {"compression_rate": 10**1000},
            {"unlisted": "value"},
        ]
        for options in invalid_options:
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    prepare_context({}, {"tokenizer": "utf8_bytes", **options})


class ExactBudgetOutputTests(unittest.TestCase):
    def test_exact_token_export_never_exceeds_budget(self):
        url = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
        cache_dir = Path(
            os.environ.get(
                "TIKTOKEN_CACHE_DIR",
                os.environ.get(
                    "DATA_GYM_CACHE_DIR",
                    str(Path(tempfile.gettempdir()) / "data-gym-cache"),
                ),
            )
        )
        if not (cache_dir / hashlib.sha1(url.encode()).hexdigest()).is_file():
            self.skipTest("verified cl100k_base cache is not available locally")
        data = extraction("## Findings\nThe measured value is 24 units.\n")
        counter = make_counter("cl100k_base")
        full = prepare_context(data, {"tokenizer": "cl100k_base", "max_tokens": 1000})
        budget = full["stats"]["output_tokens"] - 1
        result = prepare_context(data, {"tokenizer": "cl100k_base", "max_tokens": budget})
        self.assertLessEqual(counter.count(result["content"]), budget)
        self.assertEqual(result["stats"]["output_tokens"], counter.count(result["content"]))
