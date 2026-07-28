import subprocess
import tempfile
import unittest
from pathlib import Path

from paper_organizer.application.local_ai import LocalAiAssessmentService
from paper_organizer.core.model_recommendation import (
    load_model_catalog,
    recommend_models,
)
from paper_organizer.infra.hardware import GpuInfo, HardwareInspector, HardwareProfile
from paper_organizer.infra.ollama_runtime import (
    InstalledOllamaModel,
    OllamaRuntimeInspector,
    OllamaRuntimeStatus,
)
from paper_organizer.infra.settings import AppSettings, load_settings, save_settings


def hardware(
    total_ram: float = 16,
    available_ram: float = 14,
    disk: float = 100,
    gpus: tuple[GpuInfo, ...] = (),
) -> HardwareProfile:
    return HardwareProfile(
        detected_at="2026-07-23T00:00:00+00:00",
        cpu_model="Test CPU",
        physical_cores=8,
        logical_cores=16,
        memory_total_gb=total_ram,
        memory_available_gb=available_ram,
        gpus=gpus,
        model_disk_path="C:/models",
        model_disk_free_gb=disk,
    )


def ollama(*names: str) -> OllamaRuntimeStatus:
    return OllamaRuntimeStatus(
        reachable=True,
        version="0.test",
        models=tuple(
            InstalledOllamaModel(name, 2.5, "4B", "Q4_K_M", "")
            for name in names
        ),
    )


class FakeHardwareInspector:
    def __init__(self, value: HardwareProfile):
        self.value = value

    def inspect(self):
        return self.value


class FakeOllamaInspector:
    def __init__(self, value: OllamaRuntimeStatus):
        self.value = value

    def inspect(self):
        return self.value


class LocalAiTests(unittest.TestCase):
    def test_cross_family_benchmark_models_are_in_catalog(self):
        version, specs = load_model_catalog()
        models = {spec.model_id: spec for spec in specs}

        self.assertEqual(version, "2026.07.28")
        self.assertEqual(models["phi4-mini"].parameters_b, 3.84)
        self.assertEqual(models["gemma3:4b-it-qat"].download_gb, 4.0)
        self.assertEqual(
            models["ministral-3:3b-instruct-2512-q4_K_M"].parameters_b,
            3.85,
        )

    def test_nvidia_smi_output_is_parsed_without_importing_ai_runtime(self):
        def run(command, timeout):
            self.assertEqual(command[0], "nvidia-smi")
            self.assertEqual(timeout, 4.0)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="NVIDIA RTX Test, 12288, 10240\n",
                stderr="",
            )

        detected = HardwareInspector(run)._gpus()

        self.assertEqual(len(detected), 1)
        self.assertEqual(detected[0].backend, "CUDA")
        self.assertEqual(detected[0].vram_total_gb, 12.0)
        self.assertEqual(detected[0].vram_available_gb, 10.0)

    def test_ollama_runtime_reads_version_models_and_actual_sizes(self):
        def fetch(url, timeout):
            self.assertEqual(timeout, 1.5)
            if url.endswith("/api/version"):
                return {"version": "0.12.0"}
            return {
                "models": [
                    {
                        "name": "qwen3:4b",
                        "size": 2_500_000_000,
                        "modified_at": "today",
                        "details": {
                            "parameter_size": "4.0B",
                            "quantization_level": "Q4_K_M",
                        },
                    }
                ]
            }

        status = OllamaRuntimeInspector(fetch).inspect()

        self.assertTrue(status.reachable)
        self.assertEqual(status.version, "0.12.0")
        self.assertEqual(status.models[0].name, "qwen3:4b")
        self.assertEqual(status.models[0].size_gb, 2.5)

    def test_auto_profile_prefers_a_compatible_installed_model(self):
        recommendation = recommend_models(
            hardware(total_ram=32, available_ram=28),
            ollama("qwen3:4b"),
            profile="auto",
        )
        self.assertEqual(recommendation.recommended.spec.model_id, "qwen3:4b")
        self.assertTrue(recommendation.recommended.installed)

    def test_balanced_profile_recommends_8b_with_safe_16gb_headroom(self):
        recommendation = recommend_models(
            hardware(total_ram=16, available_ram=14),
            ollama(),
            profile="balanced",
        )
        self.assertEqual(recommendation.recommended.spec.model_id, "qwen3:8b")
        self.assertEqual(recommendation.recommended.rating, "권장")

    def test_low_memory_auto_profile_stays_with_ultralight_model(self):
        recommendation = recommend_models(
            hardware(total_ram=8, available_ram=6),
            ollama(),
            profile="auto",
        )
        self.assertEqual(recommendation.recommended.spec.model_id, "qwen3:1.7b")
        self.assertTrue(
            all(item.spec.parameters_b <= 8 for item in recommendation.candidates)
        )

    def test_manual_profile_does_not_offer_removed_large_model(self):
        recommendation = recommend_models(
            hardware(total_ram=8, available_ram=6),
            ollama(),
            profile="manual",
            selected_model="qwen3:14b",
        )
        self.assertIsNone(recommendation.recommended)

    def test_scan_persists_snapshot_but_does_not_change_selected_model(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            save_settings(
                AppSettings(selected_model="user:model", model_profile="balanced"),
                path,
            )
            service = LocalAiAssessmentService(
                path,
                hardware=FakeHardwareInspector(hardware()),
                ollama=FakeOllamaInspector(ollama()),
            )

            assessment = service.scan(profile="quality")
            saved = load_settings(path)

            self.assertEqual(saved.selected_model, "user:model")
            self.assertEqual(saved.model_profile, "balanced")
            self.assertEqual(saved.recommended_model, "qwen3:8b")
            self.assertEqual(saved.model_catalog_version, "2026.07.28")
            self.assertEqual(saved.hardware_profile["cpu_model"], "Test CPU")
            self.assertEqual(
                saved.hardware_profile["recommendation_profile"], "quality"
            )
            self.assertEqual(
                assessment.recommendation.recommended.spec.model_id,
                "qwen3:8b",
            )


if __name__ == "__main__":
    unittest.main()
