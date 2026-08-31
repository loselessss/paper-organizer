"""Guard catalog download fixes and bounded URL audits without network access."""

import io
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from scripts.check_model_downloads import DEFAULT_CATALOG, check_model


class Response(io.BytesIO):
    def __init__(self, payload=b"", status=200):
        super().__init__(payload)
        self.status = status
        self.headers = {"Content-Length": "1000000000"}
        self.read_sizes = []

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class ModelDownloadUrlTests(unittest.TestCase):
    def test_catalog_downloads_pin_revisions_and_sha256(self):
        catalog = json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(len(catalog["models"]), 12)
        for model in catalog["models"]:
            with self.subTest(model=model["id"]):
                self.assertRegex(model["download_url"], r"^https://huggingface\.co/[^/]+/[^/]+/resolve/[0-9a-f]{40}/[^/]+\.gguf$")
                self.assertRegex(model["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(model["download_gb"], 0)

    def test_corrected_filenames_and_qwen_repository(self):
        models = {m["id"]: m for m in json.loads(DEFAULT_CATALOG.read_text(encoding="utf-8"))["models"]}
        self.assertIn("/bartowski/Qwen_Qwen3-1.7B-GGUF/", models["qwen3:1.7b"]["download_url"])
        for model, filename in (
            ("qwen3:1.7b", "Qwen_Qwen3-1.7B-Q4_K_M.gguf"),
            ("phi4-mini", "Phi-4-mini-instruct-q4_k_m.gguf"),
            ("gemma3:4b-it-qat", "google_gemma-3-4b-it-qat-Q4_K_M.gguf"),
        ):
            self.assertTrue(models[model]["download_url"].endswith("/" + filename))

    def test_audit_checks_magic_but_never_reads_full_model(self):
        for status in (200, 206):
            with self.subTest(status=status):
                body = Response(b"GGUFmodel content", status)
                opener = Mock(side_effect=[Response(), body])
                result = check_model({"id": "test", "download_url": "https://example.test/model.gguf"}, opener=opener)
                self.assertTrue(result["ok"])
                self.assertEqual(result["size_bytes"], 1000000000)
                self.assertEqual(body.read_sizes, [4])
                self.assertTrue(body.closed)
                self.assertEqual(opener.call_args_list[0].args[0].get_method(), "HEAD")
                self.assertEqual(opener.call_args_list[1].args[0].get_header("Range"), "bytes=0-3")

    def test_audit_detects_404_without_get_request(self):
        opener = Mock(side_effect=HTTPError("url", 404, "Not Found", {}, None))
        result = check_model({"id": "test", "download_url": "https://example.test/model.gguf"}, opener=opener)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 404)
        opener.assert_called_once()

    def test_audit_rejects_html_response(self):
        opener = Mock(side_effect=[Response(), Response(b"<html>Not a model</html>")])
        result = check_model({"id": "test", "download_url": "https://example.test/model.gguf"}, opener=opener)
        self.assertFalse(result["ok"])
        self.assertIn("GGUF", result["error"])


if __name__ == "__main__":
    unittest.main()
