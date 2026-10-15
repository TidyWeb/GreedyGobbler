from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pipeline.ai_compressor as ai_compressor
from pipeline.ai_compressor import compress_prose_blocks
from pipeline.context import prepare_context
from pipeline.context_models import ContextBlock


ROOT = Path(__file__).resolve().parents[1]


def block(block_id: str, text: str, kind: str = "paragraph") -> ContextBlock:
    return ContextBlock(
        block_id=block_id,
        source_id="S1",
        unit_id="S1-U0001",
        locator="page 1",
        heading_path=("Evidence",),
        text=text,
        kind=kind,
        order=1,
    )


class CompressorTests(unittest.TestCase):
    def test_context_reverts_whole_export_inflation_for_same_selection(self):
        original = "A short description of the routine process."
        extraction = {
            "raw": original,
            "cleaned": original,
            "sources": [{"id": "S1", "name": "Doc", "uri": "doc.md"}],
            "units": [
                {
                    "id": "S1-U0001",
                    "source_id": "S1",
                    "locator": "page 1",
                    "raw": original,
                    "text": original,
                }
            ],
        }

        def expanding_compressor(blocks, **kwargs):
            paragraph = next(item for item in blocks if item.kind == "paragraph")
            return {paragraph.block_id: original + (" added words" * 30)}, []

        with patch("pipeline.context.compress_prose_blocks", side_effect=expanding_compressor):
            result = prepare_context(
                extraction,
                {
                    "tokenizer": "utf8_bytes",
                    "max_tokens": 2000,
                    "compression": "llmlingua2",
                    "compressor_model": "/fake/local/model",
                },
            )
        self.assertIn(original, result["content"])
        self.assertNotIn("added words", result["content"])
        self.assertTrue(
            any("increased export size" in warning for warning in result["warnings"])
        )

    def test_context_keeps_more_evidence_when_compressed_set_needs_budget(self):
        first = "Alpha observation " + ("routine detail " * 20) + "end."
        second = "Beta observation " + ("routine detail " * 20) + "end."
        markdown = first + "\n\n" + second
        extraction = {
            "raw": markdown,
            "cleaned": markdown,
            "sources": [{"id": "S1", "name": "Doc", "uri": "doc.md"}],
            "units": [
                {
                    "id": "S1-U0001",
                    "source_id": "S1",
                    "locator": "page 1",
                    "raw": markdown,
                    "text": markdown,
                }
            ],
        }

        def shrinking_compressor(blocks, **kwargs):
            paragraphs = [item for item in blocks if item.kind == "paragraph"]
            return {
                paragraphs[0].block_id: "Alpha observation retained.",
                paragraphs[1].block_id: "Beta observation retained.",
            }, []

        with patch("pipeline.context.compress_prose_blocks", side_effect=shrinking_compressor):
            result = prepare_context(
                extraction,
                {
                    "tokenizer": "utf8_bytes",
                    "max_tokens": 300,
                    "compression": "llmlingua2",
                    "compressor_model": "/fake/local/model",
                },
            )
        self.assertEqual(len(result["selected"]), 2)
        self.assertIn("Alpha observation retained.", result["content"])
        self.assertIn("Beta observation retained.", result["content"])
        self.assertLessEqual(
            result["stats"]["output_tokens"], result["stats"]["budget_tokens"]
        )

    def test_fake_backend_only_receives_unprotected_prose(self):
        eligible = block("b1", "A calm sentence about the general process.")
        protected = block("b2", "The dose is 12 mg and is NOT optional.")
        quoted = block("b3", 'The exact phrase is "keep this wording".')
        table = block("b4", "| Rate | 12 mg |", "table")
        calls = []

        def fake(text, rate):
            calls.append((text, rate))
            return "Compressed sentence."

        replacements, warnings = compress_prose_blocks(
            [eligible, protected, quoted, table],
            model_path="fake-model",
            rate=0.4,
            count_tokens=len,
            backend=fake,
        )
        self.assertEqual(replacements, {"b1": "Compressed sentence."})
        self.assertEqual(len(calls), 1)
        self.assertIn("experimental and lossy", warnings[0])
        self.assertTrue(any("Skipped compression" in warning for warning in warnings))

    def test_inflation_keeps_original_and_backend_errors_are_actionable(self):
        eligible = block("b1", "Short prose.")
        replacements, warnings = compress_prose_blocks(
            [eligible],
            model_path="fake-model",
            rate=0.5,
            count_tokens=len,
            backend=lambda text, rate: text + " much longer text",
        )
        self.assertEqual(replacements, {})
        self.assertTrue(any("inflated" in warning for warning in warnings))
        with self.assertRaisesRegex(ValueError, "compression failed"):
            compress_prose_blocks(
                [eligible],
                model_path="fake-model",
                rate=0.5,
                count_tokens=len,
                backend=lambda text, rate: (_ for _ in ()).throw(RuntimeError("offline")),
            )

    def test_missing_model_path_fails_without_loading_optional_package(self):
        with patch("pipeline.ai_compressor.importlib.import_module") as importer:
            with self.assertRaisesRegex(ValueError, "existing local model directory"):
                ai_compressor._load_llmlingua_backend("/path/that/does/not/exist")
            importer.assert_not_called()

    def test_model_loader_uses_cpu_local_only_and_disables_remote_code(self):
        class FakeCompressor:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.model_name = kwargs["model_name"]
                self.model = SimpleNamespace(
                    config=SimpleNamespace(model_type="bert")
                )

            def compress_prompt(self, text, rate):
                return {"compressed_prompt": text}

        saved_path = ai_compressor._BACKEND_CACHE_PATH
        saved_backend = ai_compressor._BACKEND_CACHE
        ai_compressor._BACKEND_CACHE_PATH = None
        ai_compressor._BACKEND_CACHE = None
        self.addCleanup(setattr, ai_compressor, "_BACKEND_CACHE_PATH", saved_path)
        self.addCleanup(setattr, ai_compressor, "_BACKEND_CACHE", saved_backend)
        with patch("pipeline.ai_compressor._ensure_llmlingua_tokenizer"), patch(
            "pipeline.ai_compressor.importlib.import_module",
            return_value=SimpleNamespace(PromptCompressor=FakeCompressor),
        ) as importer:
            first = ai_compressor._load_llmlingua_backend(str(ROOT))
            second = ai_compressor._load_llmlingua_backend(str(ROOT))
        self.assertIs(first, second)
        importer.assert_called_once_with("llmlingua")

        # Recreate once to retain the constructor values for direct assertions.
        constructed = []
        instances = []

        class RecordingCompressor(FakeCompressor):
            def __init__(self, **kwargs):
                constructed.append(kwargs)
                super().__init__(**kwargs)
                instances.append(self)

        ai_compressor._BACKEND_CACHE_PATH = None
        ai_compressor._BACKEND_CACHE = None
        with patch("pipeline.ai_compressor._ensure_llmlingua_tokenizer"), patch(
            "pipeline.ai_compressor.importlib.import_module",
            return_value=SimpleNamespace(PromptCompressor=RecordingCompressor),
        ):
            engine = ai_compressor._load_llmlingua_backend(str(ROOT))
        self.assertEqual(engine("hello", 0.5), "hello")
        self.assertEqual(constructed[0]["device_map"], "cpu")
        self.assertTrue(constructed[0]["model_config"]["local_files_only"])
        self.assertFalse(constructed[0]["model_config"]["trust_remote_code"])
        self.assertEqual(instances[0].model_name, "bert-base-multilingual-cased")

    def test_missing_local_cl100k_cache_blocks_upstream_compressor_initialization(self):
        cache = Path(tempfile.gettempdir()) / "context-llmlingua-empty-token-cache"
        with patch.dict(os.environ, {"TIKTOKEN_CACHE_DIR": str(cache)}):
            with patch("pipeline.ai_compressor.importlib.import_module") as importer:
                with self.assertRaisesRegex(ValueError, "requires the verified cl100k_base"):
                    ai_compressor._initialize_llmlingua(ROOT)
                importer.assert_not_called()

    def test_context_recounts_compressed_export_and_labels_experimental_loss(self):
        extraction = {
            "raw": "A careful explanation of the general process.",
            "cleaned": "A careful explanation of the general process.",
            "sources": [{"id": "S1", "name": "Doc", "uri": "doc.md"}],
            "units": [
                {
                    "id": "S1-U0001",
                    "source_id": "S1",
                    "locator": "section 1",
                    "raw": "A careful explanation of the general process.",
                    "text": "A careful explanation of the general process.",
                }
            ],
        }

        def fake_compress(blocks, **kwargs):
            paragraphs = [item for item in blocks if item.kind == "paragraph"]
            return {paragraphs[0].block_id: "Short prose."}, [
                "LLMLingua-2 compression is experimental and lossy; review the original source."
            ]

        with patch("pipeline.context.compress_prose_blocks", side_effect=fake_compress):
            result = prepare_context(
                extraction,
                {
                    "tokenizer": "utf8_bytes",
                    "max_tokens": 1000,
                    "compression": "llmlingua2",
                    "compressor_model": "/fake/local/model",
                },
            )
        self.assertIn("Short prose.", result["content"])
        self.assertIn("Learned compression is experimental and lossy", result["content"])
        self.assertNotIn("A careful explanation", result["content"])
        self.assertLessEqual(
            result["stats"]["output_tokens"], result["stats"]["budget_tokens"]
        )
        self.assertTrue(any("experimental and lossy" in item for item in result["warnings"]))
