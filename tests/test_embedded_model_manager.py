import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from paper_organizer.application.embedded_model_manager import (
    EmbeddedModelManagerService,
)
from paper_organizer.infra.hardware import HardwareProfile


class FakeHardware:
    def inspect(self):
        return HardwareProfile(
            detected_at="2026-08-26T00:00:00+00:00",
            cpu_model="test",
            physical_cores=4,
            logical_cores=8,
            memory_total_gb=16,
            memory_available_gb=8,
            gpus=(),
            model_disk_path="models",
            model_disk_free_gb=20,
        )


class FakeResponse(BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()


def write_catalog(path: Path, *, url: str = "", sha256: str = "") -> None:
    path.write_text(
        json.dumps(
            {
                "catalog_version": "test",
                "models": [
                    {
                        "id": "qwen3:1.7b",
                        "label": "Qwen3 1.7B",
                        "parameters_b": 1.7,
                        "download_gb": 0.01,
                        "runtime_memory_gb": 2,
                        "minimum_ram_gb": 8,
                        "recommended_ram_gb": 16,
                        "tier": "background",
                        "quality": 3,
                        "license": "Apache-2.0",
                        "recommended_context": 8192,
                        "download_url": url,
                        "sha256": sha256,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


class EmbeddedModelManagerTests(unittest.TestCase):
    def test_plan_blocks_catalog_entry_without_direct_url(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog.json"
            write_catalog(catalog)
            service = EmbeddedModelManagerService(
                root / "settings.json",
                catalog_path=catalog,
                hardware=FakeHardware(),
                model_dir=root / "models",
            )

            plan = service.plan_download("qwen3:1.7b")

        self.assertFalse(plan.can_download)
        self.assertIn("다운로드 주소", plan.reason)

    def test_download_writes_gguf_atomically_and_selects_local_provider(self):
        payload = b"fake gguf"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog = root / "catalog.json"
            write_catalog(catalog, url="https://example.test/model.gguf", sha256=digest)
            service = EmbeddedModelManagerService(
                root / "settings.json",
                catalog_path=catalog,
                hardware=FakeHardware(),
                model_dir=root / "models",
                opener=lambda _url: FakeResponse(payload),
            )

            target = service.download("qwen3:1.7b")

            saved = json.loads((root / "settings.json").read_text(encoding="utf-8"))
            target_name = target.name
            target_bytes = target.read_bytes()

        self.assertEqual(target_name, "qwen3_1.7b.gguf")
        self.assertEqual(target_bytes, payload)
        self.assertEqual(saved["summary_provider"], "local")
        self.assertEqual(saved["selected_model"], "qwen3:1.7b")
        self.assertEqual(saved["background_model"], "qwen3:1.7b")


if __name__ == "__main__":
    unittest.main()
