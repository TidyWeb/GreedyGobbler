import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from app import create_app


ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / ".test-artifacts"


def extraction_result(raw="Raw source", cleaned="Clean source"):
    return {
        "raw": raw,
        "cleaned": cleaned,
        "sources": [{"id": "S1", "name": "sample.txt", "uri": "sample.txt"}],
        "units": [{"id": "S1-U0001", "source_id": "S1", "locator": "section 1", "raw": raw, "text": cleaned}],
        "warnings": [],
        "omissions": [],
        "metrics": {"unit_count": 1},
    }


def packed_result(content="## Context\n\nSelected evidence"):
    return {
        "content": content,
        "stats": {
            "raw_tokens": 20,
            "cleaned_tokens": 10,
            "output_tokens": 8,
            "budget_tokens": 8000,
            "tokenizer": "cl100k_base",
            "count_kind": "tokens",
            "reduction_percent": 60,
        },
        "sources": [{"id": "S1", "name": "sample.txt", "uri": "sample.txt"}],
        "selected": [{"block_id": "S1-U0001-B001", "source_id": "S1", "unit_id": "S1-U0001", "locator": "section 1"}],
        "omitted": [],
        "warnings": [],
        "outline": ["Context", "Evidence"],
    }


class AppInterfaceTests(unittest.TestCase):
    def setUp(self):
        self.case_dir = ARTIFACT_ROOT / f"{self._testMethodName}-{uuid.uuid4().hex[:8]}"
        self.save_dir = self.case_dir / "saved"
        self.temp_dir = self.case_dir / "uploads"
        self.extractor_calls = []
        self.packer_calls = []

        def extractor(source, **kwargs):
            self.extractor_calls.append((source, kwargs))
            callback = kwargs.get("progress")
            if callback:
                callback({"pages_done": 1, "pages_queued": 2})
            return extraction_result()

        def packer(extraction, options):
            self.packer_calls.append((extraction, options))
            return packed_result()

        self.extractor = extractor
        self.packer = packer
        self.app = create_app({
            "TESTING": True,
            "SAVE_DIR": str(self.save_dir),
            "TEMP_DIR": str(self.temp_dir),
            "EXTRACTOR": self.extractor,
            "CONTEXT_PREPARER": self.packer,
            "COMPRESSOR_MODEL": None,
        })
        self.client = self.app.test_client()

    def process_url(self, options=None, **extra):
        body = {"source": "https://example.test/article", "is_url": True, **extra}
        if options is not None:
            body["options"] = options
        return self.client.post("/process", json=body)

    def test_factory_does_not_create_data_directories_and_is_configurable(self):
        self.assertFalse(self.save_dir.exists())
        self.assertFalse(self.temp_dir.exists())
        response = self.process_url({"max_tokens": 1200, "mode": "focused", "query": "evidence"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.extractor_calls[0][1]["profile"], "document")
        self.assertEqual(self.extractor_calls[0][1]["trim_body"], False)
        self.assertEqual(self.extractor_calls[0][1]["allow_remote"], False)
        self.assertEqual(self.extractor_calls[0][1]["use_browser"], False)
        self.assertEqual(self.packer_calls[0][1]["max_tokens"], 1200)
        self.assertEqual(self.packer_calls[0][1]["mode"], "focused")
        self.assertEqual(self.packer_calls[0][1]["query"], "evidence")
        self.assertFalse(self.save_dir.exists())
        self.assertFalse(self.temp_dir.exists())

    def test_json_response_preserves_extraction_and_context_fields(self):
        response = self.process_url({"profile": "web", "allow_remote": True, "depth": 2})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["raw"], "Raw source")
        self.assertEqual(data["cleaned"], "Clean source")
        self.assertEqual(data["content"], "## Context\n\nSelected evidence")
        self.assertEqual(data["stats"]["output_tokens"], 8)
        self.assertEqual(data["sources"][0]["uri"], "sample.txt")
        self.assertEqual(data["selected"][0]["source_id"], "S1")
        self.assertEqual(data["outline"], ["Context", "Evidence"])
        self.assertTrue(self.extractor_calls[0][1]["allow_remote"])
        self.assertEqual(self.extractor_calls[0][1]["depth"], 2)
        self.assertEqual(self.client.get("/progress").get_json()["status"], "finished")

    def test_response_keeps_full_previews_without_duplicating_unit_bodies(self):
        response = self.process_url()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["raw"], "Raw source")
        self.assertEqual(data["cleaned"], "Clean source")
        unit = data["extraction"]["units"][0]
        self.assertEqual(
            unit, {"id": "S1-U0001", "source_id": "S1", "locator": "section 1"}
        )
        self.assertNotIn("raw", unit)
        self.assertNotIn("text", unit)

    def test_legacy_top_level_depth_is_supported(self):
        response = self.client.post("/process", json={
            "source": "https://example.test/legacy", "is_url": True, "depth": 2,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.extractor_calls[0][1]["depth"], 2)

    def test_malformed_json_and_wrong_option_types_return_consistent_json(self):
        bad_json = self.client.post("/process", data="{", content_type="application/json")
        self.assertEqual(bad_json.status_code, 400)
        self.assertEqual(bad_json.get_json()["status"], "error")
        self.assertIn("valid JSON", bad_json.get_json()["message"])

        for options in (
            {"depth": True},
            {"max_tokens": "8000"},
            {"trim_body": 1},
            {"context_window": False},
            {"reserved_tokens": -1},
            {"tokenizer": "unknown"},
            {"compressor_model": "/client/path"},
        ):
            with self.subTest(options=options):
                response = self.process_url(options)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["status"], "error")
        ignored_top_level = self.client.post("/process", json={
            "source": "https://example.test/article", "is_url": True, "max_tokens": 500,
        })
        self.assertEqual(ignored_top_level.status_code, 400)
        self.assertEqual(self.extractor_calls, [])

    def test_invalid_source_and_non_url_json_source_are_rejected(self):
        for body in (
            {"source": 42, "is_url": True},
            {"source": "file:///etc/passwd", "is_url": True},
            {"source": "/tmp/private.pdf", "is_url": False},
            {"source": "https://user:secret@example.test/x", "is_url": True},
        ):
            with self.subTest(body=body):
                response = self.client.post("/process", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["status"], "error")

    def test_repeated_same_name_uploads_use_unique_retained_paths(self):
        first = self.client.post("/process", data={
            "file": (io.BytesIO(b"first body"), "same.txt"),
            "options": json.dumps({"max_tokens": 900}),
        }, content_type="multipart/form-data")
        second = self.client.post("/process", data={
            "file": (io.BytesIO(b"second body"), "same.txt"),
            "options": json.dumps({"max_tokens": 900}),
        }, content_type="multipart/form-data")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        first_path = Path(self.extractor_calls[0][0])
        second_path = Path(self.extractor_calls[1][0])
        self.assertNotEqual(first_path, second_path)
        self.assertTrue(first_path.exists())
        self.assertTrue(second_path.exists())
        self.assertEqual(first_path.read_bytes(), b"first body")
        self.assertEqual(second_path.read_bytes(), b"second body")
        self.assertEqual(self.extractor_calls[0][1]["is_url"], False)
        self.assertEqual(self.packer_calls[0][0]["sources"][0]["name"], "same.txt")
        self.assertEqual(self.packer_calls[0][0]["sources"][0]["uri"], "same.txt")

    def test_oversized_upload_returns_json_413(self):
        limited = create_app({
            "TESTING": True, "MAX_CONTENT_LENGTH": 256,
            "SAVE_DIR": str(self.save_dir), "TEMP_DIR": str(self.temp_dir),
            "EXTRACTOR": self.extractor, "CONTEXT_PREPARER": self.packer,
        }).test_client()
        response = limited.post("/process", data={
            "file": (io.BytesIO(b"x" * 1024), "large.txt"),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["status"], "error")
        self.assertIn("size limit", response.get_json()["message"])

    def test_save_rejects_traversal_and_symlink_target(self):
        for filename in ("../escape.md", "/tmp/escape.md", "nested/name.md", r"nested\name.md", "..hidden.md"):
            with self.subTest(filename=filename):
                response = self.client.post("/download", json={"filename": filename, "content": "x"})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["status"], "error")
        self.save_dir.mkdir(parents=True)
        outside = self.case_dir / "outside.md"
        outside.write_text("keep me", encoding="utf-8")
        (self.save_dir / "linked.md").symlink_to(outside)
        response = self.client.post("/download", json={"filename": "linked.md", "content": "overwrite"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep me")

    def test_save_collisions_create_suffixes_without_overwriting(self):
        first = self.client.post("/download", json={"filename": "notes.md", "content": "first"})
        second = self.client.post("/download", json={"filename": "notes.md", "content": "second"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_path = Path(first.get_json()["path"])
        second_path = Path(second.get_json()["path"])
        self.assertEqual(first_path.name, "notes.md")
        self.assertEqual(second_path.name, "notes-2.md")
        self.assertEqual(first_path.read_text(encoding="utf-8"), "first")
        self.assertEqual(second_path.read_text(encoding="utf-8"), "second")

    def test_each_factory_uses_its_own_save_and_temp_directories(self):
        alternate = self.case_dir / "alternate"
        second_app = create_app({
            "TESTING": True, "SAVE_DIR": str(alternate / "save"), "TEMP_DIR": str(alternate / "temp"),
            "EXTRACTOR": self.extractor, "CONTEXT_PREPARER": self.packer,
        })
        response_a = self.client.post("/download", json={"filename": "a.md", "content": "A"})
        response_b = second_app.test_client().post("/download", json={"filename": "b.md", "content": "B"})
        self.assertEqual(response_a.status_code, 200)
        self.assertEqual(response_b.status_code, 200)
        self.assertEqual(Path(response_a.get_json()["path"]).parent, self.save_dir)
        self.assertEqual(Path(response_b.get_json()["path"]).parent, alternate / "save")
        self.assertEqual(second_app.test_client().get("/progress").get_json()["status"], "idle")

    def test_extractor_and_packer_errors_release_single_job_lock_and_hide_tracebacks(self):
        self.app.config["EXTRACTOR"] = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("private traceback"))
        failed = self.process_url()
        self.assertEqual(failed.status_code, 500)
        self.assertNotIn("private traceback", failed.get_json()["message"])
        self.assertEqual(self.client.get("/progress").get_json()["status"], "error")

        self.app.config["EXTRACTOR"] = self.extractor
        succeeding = self.process_url()
        self.assertEqual(succeeding.status_code, 200)
        self.assertEqual(self.client.get("/progress").get_json()["status"], "finished")

    def test_conversion_value_error_is_returned_as_safe_json_and_lock_is_released(self):
        self.app.config["EXTRACTOR"] = lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("Unsupported file type"))
        response = self.process_url()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"status": "error", "message": "Unsupported file type"})
        self.app.config["EXTRACTOR"] = self.extractor
        self.assertEqual(self.process_url().status_code, 200)

    def test_injected_model_path_is_server_only(self):
        self.app.config["COMPRESSOR_MODEL"] = "/srv/models/local-model"
        response = self.process_url({"compression": "llmlingua2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.packer_calls[-1][1]["compressor_model"], "/srv/models/local-model")
        client_attempt = self.process_url({"compression": "llmlingua2", "compressor_model": "/client/model"})
        self.assertEqual(client_attempt.status_code, 400)
        self.assertEqual(self.packer_calls.__len__(), 1)

    def test_foreign_origin_is_rejected_and_same_origin_is_accepted(self):
        response = self.client.post("/download", json={"filename": "blocked.md", "content": "x"}, headers={"Origin": "https://attacker.test"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.save_dir.exists())
        accepted = self.client.post("/download", json={"filename": "allowed.md", "content": "x"}, headers={"Origin": "http://localhost"})
        self.assertEqual(accepted.status_code, 200)

    def test_progress_resets_for_each_request_and_is_updated_by_extractor(self):
        first = self.process_url()
        self.assertEqual(first.status_code, 200)
        progress = self.client.get("/progress").get_json()
        self.assertEqual(progress["pages_done"], 1)
        self.assertEqual(progress["pages_queued"], 2)
        self.assertEqual(progress["status"], "finished")
        self.app.config["EXTRACTOR"] = lambda *args, **kwargs: {**extraction_result(), "warnings": []}
        second = self.process_url()
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.client.get("/progress").get_json()["status"], "finished")

    def test_overlapping_processing_request_returns_409(self):
        entered = threading.Event()
        release = threading.Event()

        def blocking_extractor(*args, **kwargs):
            entered.set()
            release.wait(timeout=5)
            return extraction_result()

        self.app.config["EXTRACTOR"] = blocking_extractor
        result = {}

        def first_request():
            with self.app.test_client() as thread_client:
                result["response"] = thread_client.post("/process", json={
                    "source": "https://example.test/slow", "is_url": True,
                })

        thread = threading.Thread(target=first_request)
        thread.start()
        self.assertTrue(entered.wait(timeout=3))
        overlapping = self.process_url()
        self.assertEqual(overlapping.status_code, 409)
        release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["response"].status_code, 200)

    def test_count_endpoint_labels_utf8_byte_bound_and_rejects_wrong_types(self):
        response = self.client.post("/count", json={"text": "é", "tokenizer": "utf8_bytes"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 2)
        self.assertEqual(response.get_json()["count_kind"], "utf8_bytes")
        invalid = self.client.post("/count", json={"text": 4, "tokenizer": "cl100k_base"})
        self.assertEqual(invalid.status_code, 400)

    def test_exact_count_endpoint_uses_shared_counter_contract_with_a_fake_service(self):
        calls = []
        source_text = "literal <|endoftext|>"

        class FakeCounter:
            def count(self, text):
                calls.append(("count", text))
                return 17

        def fake_make_counter(tokenizer):
            calls.append(("make_counter", tokenizer))
            return FakeCounter()

        fake_token_budget = SimpleNamespace(make_counter=fake_make_counter)
        with patch.dict("sys.modules", {"pipeline.token_budget": fake_token_budget}):
            response = self.client.post(
                "/count", json={"text": source_text, "tokenizer": "cl100k_base"}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["count"], 17)
        self.assertEqual(response.get_json()["count_kind"], "tokens")
        self.assertEqual(calls, [("make_counter", "cl100k_base"), ("count", source_text)])

    def test_empty_extraction_is_reported_as_failure(self):
        self.app.config["EXTRACTOR"] = lambda *_args, **_kwargs: extraction_result("", "")
        response = self.process_url()
        self.assertEqual(response.status_code, 400)
        self.assertIn("No readable content", response.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
