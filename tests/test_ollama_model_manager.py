import json
import tempfile
import unittest
from pathlib import Path
from threading import Event

from paper_organizer.application.ollama_model_manager import (
    OllamaModelManagerService,
)
from paper_organizer.infra.hardware import HardwareProfile
from paper_organizer.infra.ollama_models import (
    OllamaModelClient,
    OllamaModelError,
    OllamaOperationCancelled,
    OllamaPullProgress,
    OllamaVerification,
)
from paper_organizer.infra.ollama_runtime import (
    InstalledOllamaModel,
    OllamaRuntimeStatus,
)
from paper_organizer.infra.settings import AppSettings, load_settings, save_settings


class FakeResponse:
    def __init__(self, data=b"", *, lines=(), status=200):
        self.status = status
        self._data = data
        self._lines = iter(lines)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._data

    def readline(self):
        return next(self._lines, b"")


class QueueOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


def model(name="qwen3:4b"):
    return InstalledOllamaModel(name, 2.5, "4.0B", "Q4_K_M", "today")


def profile(disk=100):
    return HardwareProfile(
        detected_at="2026-07-23T00:00:00+00:00",
        cpu_model="Test CPU",
        physical_cores=8,
        logical_cores=16,
        memory_total_gb=16,
        memory_available_gb=12,
        gpus=(),
        model_disk_path="C:/models",
        model_disk_free_gb=disk,
    )


class FakeHardware:
    def __init__(self, value):
        self.value = value

    def inspect(self):
        return self.value


class FakeRuntime:
    def __init__(self, *models, reachable=True):
        self.value = OllamaRuntimeStatus(reachable, "0.test", tuple(models), "")

    def inspect(self):
        return self.value


class FakeClient:
    def __init__(self, *, verification=None, error=None):
        self.verification = verification or OllamaVerification(
            model(), True, "verified"
        )
        self.error = error
        self.pulled = []
        self.deleted = []

    def pull(self, model_id, *, on_progress=None, cancel=None):
        self.pulled.append(model_id)
        if on_progress:
            on_progress(OllamaPullProgress("success", 10, 10))

    def verify(self, model_id):
        if self.error:
            raise self.error
        return self.verification

    def delete(self, model_id):
        self.deleted.append(model_id)


class OllamaModelClientTests(unittest.TestCase):
    def test_pull_progress_and_json_verification(self):
        opener = QueueOpener(
            [
                FakeResponse(
                    lines=(
                        b'{"status":"pulling","completed":50,"total":100}\n',
                        b'{"status":"success","completed":100,"total":100}\n',
                    )
                ),
                FakeResponse(
                    json.dumps(
                        {
                            "models": [
                                {
                                    "name": "qwen3:4b",
                                    "size": 2_500_000_000,
                                    "details": {
                                        "parameter_size": "4.0B",
                                        "quantization_level": "Q4_K_M",
                                    },
                                }
                            ]
                        }
                    ).encode()
                ),
                FakeResponse(b'{"response":"{\\"ready\\": true}","done":true}'),
            ]
        )
        client = OllamaModelClient(opener)
        progress = []

        result = client.pull("qwen3:4b", on_progress=progress.append)
        verified = client.verify("qwen3:4b")

        self.assertEqual(result.percent, 100)
        self.assertEqual([item.percent for item in progress], [50, 100])
        self.assertEqual(verified.model.size_gb, 2.5)
        pull_payload = json.loads(opener.requests[0][0].data)
        self.assertFalse(pull_payload["insecure"])
        generate_payload = json.loads(opener.requests[2][0].data)
        self.assertFalse(generate_payload["stream"])
        self.assertEqual(generate_payload["keep_alive"], 0)

    def test_pull_can_be_cancelled_before_next_stream_chunk(self):
        opener = QueueOpener([FakeResponse(lines=(b'{"status":"pulling"}\n',))])
        cancel = Event()
        cancel.set()

        with self.assertRaises(OllamaOperationCancelled):
            OllamaModelClient(opener).pull("qwen3:4b", cancel=cancel)

    def test_model_mutations_are_restricted_to_loopback_http(self):
        with self.assertRaises(ValueError):
            OllamaModelClient(endpoint="https://example.com")
        with self.assertRaises(ValueError):
            OllamaModelClient(endpoint="http://192.168.0.3:11434")

    def test_delete_uses_delete_and_confirms_model_disappeared(self):
        opener = QueueOpener(
            [FakeResponse(), FakeResponse(b'{"models":[]}')]
        )

        OllamaModelClient(opener).delete("qwen3:4b")

        self.assertEqual(opener.requests[0][0].get_method(), "DELETE")
        self.assertTrue(opener.requests[0][0].full_url.endswith("/api/delete"))


class OllamaModelManagerTests(unittest.TestCase):
    def test_installed_model_choices_exclude_inventory_only_large_models(self):
        service = OllamaModelManagerService(
            Path("unused.json"),
            hardware=FakeHardware(profile()),
            runtime=FakeRuntime(
                model("qwen3:4b"),
                InstalledOllamaModel(
                    "gemma3:12b",
                    8.1,
                    "12.2B",
                    "Q4_K_M",
                    "today",
                ),
            ),
            client=FakeClient(),
        )

        self.assertEqual(service.installed_models(), ("qwen3:4b",))

    def test_installed_12b_or_larger_model_is_inventory_only(self):
        with tempfile.TemporaryDirectory() as temp:
            large = InstalledOllamaModel(
                "gemma3:12b",
                8.1,
                "12.2B",
                "Q4_K_M",
                "today",
            )
            service = OllamaModelManagerService(
                Path(temp) / "settings.json",
                hardware=FakeHardware(profile()),
                runtime=FakeRuntime(large),
                client=FakeClient(),
            )

            snapshot = service.snapshot()

        entry = next(item for item in snapshot.entries if item.model_id == "gemma3:12b")
        self.assertTrue(entry.installed)
        self.assertFalse(entry.selectable)
        self.assertIn("정밀 요약", entry.usage_guidance)


    def test_install_checks_disk_then_tracks_only_verified_model(self):
        with tempfile.TemporaryDirectory() as temp:
            settings_path = Path(temp) / "settings.json"
            save_settings(AppSettings(selected_model="old:model"), settings_path)
            fake_client = FakeClient()
            service = OllamaModelManagerService(
                settings_path,
                hardware=FakeHardware(profile()),
                runtime=FakeRuntime(),
                client=fake_client,
            )

            plan = service.plan_install("qwen3:4b")
            result = service.install("qwen3:4b")
            saved = load_settings(settings_path)

        self.assertTrue(plan.can_install)
        self.assertGreater(plan.required_free_gb, plan.estimated_download_gb)
        self.assertTrue(result.newly_managed)
        self.assertEqual(saved.managed_ollama_models, ["qwen3:4b"])
        self.assertEqual(saved.selected_model, "old:model")

    def test_failed_verification_does_not_track_or_activate_model(self):
        with tempfile.TemporaryDirectory() as temp:
            settings_path = Path(temp) / "settings.json"
            save_settings(AppSettings(selected_model="old:model"), settings_path)
            service = OllamaModelManagerService(
                settings_path,
                hardware=FakeHardware(profile()),
                runtime=FakeRuntime(),
                client=FakeClient(error=OllamaModelError("bad JSON")),
            )

            with self.assertRaises(OllamaModelError):
                service.install("qwen3:4b")
            saved = load_settings(settings_path)

        self.assertEqual(saved.managed_ollama_models, [])
        self.assertEqual(saved.selected_model, "old:model")

    def test_disk_safety_blocks_download_before_pull(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_client = FakeClient()
            service = OllamaModelManagerService(
                Path(temp) / "settings.json",
                hardware=FakeHardware(profile(disk=1)),
                runtime=FakeRuntime(),
                client=fake_client,
            )

            plan = service.plan_install("qwen3:4b")
            with self.assertRaises(ValueError):
                service.install("qwen3:4b")

        self.assertFalse(plan.can_install)
        self.assertEqual(fake_client.pulled, [])

    def test_explicit_delete_clears_active_and_managed_model(self):
        with tempfile.TemporaryDirectory() as temp:
            settings_path = Path(temp) / "settings.json"
            save_settings(
                AppSettings(
                    selected_model="qwen3:4b",
                    ollama_resident_model="qwen3:4b",
                    managed_ollama_models=["qwen3:4b"],
                ),
                settings_path,
            )
            fake_client = FakeClient()
            service = OllamaModelManagerService(
                settings_path,
                hardware=FakeHardware(profile()),
                runtime=FakeRuntime(model()),
                client=fake_client,
            )

            cleared = service.delete("qwen3:4b")
            saved = load_settings(settings_path)

        self.assertTrue(cleared)
        self.assertEqual(fake_client.deleted, ["qwen3:4b"])
        self.assertEqual(saved.selected_model, "")
        self.assertEqual(saved.ollama_resident_model, "")
        self.assertEqual(saved.managed_ollama_models, [])

    def test_existing_shared_model_can_be_verified_without_becoming_managed(self):
        with tempfile.TemporaryDirectory() as temp:
            settings_path = Path(temp) / "settings.json"
            fake_client = FakeClient()
            service = OllamaModelManagerService(
                settings_path,
                hardware=FakeHardware(profile()),
                runtime=FakeRuntime(model()),
                client=fake_client,
            )

            verified = service.verify_installed("qwen3:4b")
            saved = load_settings(settings_path)

        self.assertEqual(verified.model.name, "qwen3:4b")
        self.assertEqual(fake_client.pulled, [])
        self.assertEqual(saved.managed_ollama_models, [])


if __name__ == "__main__":
    unittest.main()
