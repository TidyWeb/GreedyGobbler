"""HTTP boundaries exercising the real extractor, packer and safe fixture transport."""

from __future__ import annotations

import io
import json
import uuid
from pathlib import Path
import unittest
from unittest.mock import patch

from app import create_app
from pipeline import web_fetch

from test_integration_pipeline import ARTIFACTS, ensure_integration_fixtures


class FixtureResponse:
    def __init__(self, body: str):
        self.status_code = 200
        self.headers = {"Content-Type": "text/html; charset=utf-8"}
        self.body = body.encode("utf-8")

    def iter_content(self, chunk_size: int):
        for offset in range(0, len(self.body), chunk_size):
            yield self.body[offset : offset + chunk_size]

    def close(self):
        return None


class HttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ensure_integration_fixtures()["text"]
        self.run_root = ARTIFACTS / "http" / uuid.uuid4().hex
        self.save_root = self.run_root / "saved"
        self.upload_root = self.run_root / "uploads"
        self.app = create_app(
            {
                "TESTING": True,
                "SAVE_DIR": str(self.save_root),
                "TEMP_DIR": str(self.upload_root),
            }
        )
        self.client = self.app.test_client()

    def test_upload_process_count_progress_and_save_use_real_services(self):
        options = {
            "mode": "focused",
            "query": "yield not independently replicated",
            "max_tokens": 10000,
            "tokenizer": "utf8_bytes",
        }
        response = self.client.post(
            "/process",
            data={
                "file": (io.BytesIO(self.fixture.read_bytes()), "field-note.txt"),
                "options": json.dumps(options),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("18.4 litres per kilogram", data["raw"])
        self.assertIn("not independently replicated", data["content"])
        self.assertLessEqual(data["stats"]["output_tokens"], data["stats"]["budget_tokens"])
        self.assertTrue(data["selected"])
        self.assertTrue(all(record.get("locator") for record in data["selected"]))

        uploads = list(self.upload_root.glob("upload-*.txt"))
        self.assertEqual(len(uploads), 1)
        self.assertEqual(uploads[0].read_bytes(), self.fixture.read_bytes())
        self.assertEqual(self.client.get("/progress").get_json()["status"], "finished")

        count = self.client.post("/count", json={"text": "évidence", "tokenizer": "utf8_bytes"})
        self.assertEqual(count.status_code, 200)
        self.assertEqual(count.get_json()["count"], len("évidence".encode("utf-8")))
        self.assertEqual(count.get_json()["count_kind"], "utf8_bytes")

        saved = self.client.post(
            "/download",
            json={"filename": "integration-output.md", "content": data["content"]},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        saved_path = Path(saved.get_json()["path"])
        self.assertEqual(saved_path.parent, self.save_root)
        self.assertEqual(saved_path.read_text(encoding="utf-8"), data["content"])

    def test_invalid_request_returns_json_without_starting_processing(self):
        response = self.client.post("/process", json={"source": "", "is_url": True})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["status"], "error")
        self.assertEqual(self.client.get("/progress").get_json()["status"], "idle")

    def test_url_flow_uses_an_injected_fixture_transport_without_relaxing_safety(self):
        html = """<!doctype html><html><head><title>Local fixture article</title></head>
        <body><main><h1>Local fixture article</h1><p>
        This trusted test fixture contains more than enough article text to exercise
        extraction, source provenance, and packing through the Flask HTTP route.
        The stated value is 18.4 units and it was not independently replicated.
        </p></main></body></html>"""
        seen = []

        def fixture_transport(url, **_kwargs):
            seen.append(url)
            return FixtureResponse(html)

        options = {"max_tokens": 5000, "tokenizer": "utf8_bytes", "mode": "faithful"}
        with (
            patch.object(web_fetch, "POLITE_DELAY_SECONDS", 0),
            patch.object(web_fetch, "_resolve_public_addresses", return_value=["93.184.216.34"]),
            patch.object(web_fetch.requests, "get", side_effect=fixture_transport),
        ):
            response = self.client.post(
                "/process",
                json={"source": "https://fixture.example/article", "is_url": True, "options": options},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(seen, ["https://fixture.example/article"])
        data = response.get_json()
        self.assertIn("not independently replicated", data["raw"])
        self.assertIn("fixture.example", data["sources"][0]["uri"])
        self.assertLessEqual(data["stats"]["output_tokens"], 5000)


if __name__ == "__main__":
    unittest.main()
