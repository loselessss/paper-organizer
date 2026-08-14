import subprocess
import tempfile
import unittest
from pathlib import Path

from paper_organizer.application.local_ai import LocalAiAssessmentService
from paper_organizer.core.model_recommendation import (
    load_model_catalog,
    memory_tier_guidance,
    model_benchmark_summary,
    model_usage_guidance,
    recommendation_tier_overview,
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
    def test_model_guidance_explains_safe_roles_by_parameter_class(self):
        benchmark = model_usage_guidance("qwen3:0.6b")
        low_spec = model_usage_guidance("qwen3:1.7b")
        standard = model_usage_guidance("qwen3:4b")
        manual_default = model_usage_guidance("qwen3.5:4b")
        fast_alternative = model_usage_guidance("granite4.1:3b")
        advanced = model_usage_guidance("qwen3:8b")

        self.assertEqual(benchmark.role, "벤치마크·분류 보조")
        self.assertEqual(benchmark.hallucination_risk, "매우 높음")
        self.assertEqual(low_spec.role, "백그라운드 서지·Abstract")
        self.assertEqual(
            low_spec.summary_strategy,
            "서지정보 검증 후 Abstract만 정리",
        )
        self.assertFalse(low_spec.advanced_analysis)
        self.assertEqual(standard.role, "수동 정밀 3~4B급")
        self.assertEqual(standard.summary_strategy, "구역별 요약 후 통합")
        self.assertFalse(standard.advanced_analysis)
        self.assertEqual(manual_default.role, "수동 본문 분석 기본")
        self.assertIn("Granite 4.1 3B보다", manual_default.caution)
        self.assertEqual(fast_alternative.role, "빠른 수동 요약 대안")
        self.assertIn("약 25%", fast_alternative.caution)
        self.assertEqual(advanced.role, "고급 분석 8B+")
        self.assertTrue(advanced.advanced_analysis)
        self.assertIn("충분한 RAM", advanced.caution)

    def test_cross_family_benchmark_models_are_in_catalog(self):
        version, specs = load_model_catalog()
        models = {spec.model_id: spec for spec in specs}

        self.assertEqual(version, "2026.08.01.2")
        self.assertEqual(
            tuple(spec.model_id for spec in specs[:2]),
            ("qwen3.5:2b", "qwen3.5:4b"),
        )
        self.assertEqual(models["qwen3.5:2b"].download_gb, 2.7)
        self.assertEqual(models["qwen3.5:2b"].download_priority, 1)
        self.assertEqual(models["qwen3.5:4b"].download_gb, 3.4)
        self.assertEqual(models["qwen3.5:4b"].download_priority, 2)
        self.assertEqual(models["granite3.3:2b"].parameters_b, 2.5)
        self.assertEqual(models["granite3.3:2b"].download_gb, 1.55)
        self.assertEqual(models["granite4.1:3b"].parameters_b, 3.0)
        self.assertEqual(models["granite4.1:3b"].download_gb, 2.1)
        self.assertEqual(models["granite4.1:3b"].recommendation_rank, 2)
        self.assertEqual(models["granite4.1:3b"].benchmark_score, 25.83)
        self.assertEqual(models["qwen3.5:4b"].recommendation_rank, 1)
        self.assertEqual(models["qwen3.5:4b"].benchmark_score, 33.92)
        self.assertEqual(models["qwen3:4b"].recommendation_rank, 3)
        self.assertEqual(models["qwen3:4b"].benchmark_score, 80.8)
        self.assertEqual(models["phi4-mini"].parameters_b, 3.84)
        self.assertEqual(models["gemma3:4b-it-qat"].download_gb, 4.0)
        self.assertEqual(
            models["ministral-3:3b-instruct-2512-q4_K_M"].parameters_b,
            3.85,
        )

    def test_benchmark_summary_explains_rank_score_speed_and_strengths(self):
        _, specs = load_model_catalog()
        granite = next(spec for spec in specs if spec.model_id == "granite4.1:3b")

        summary = model_benchmark_summary(granite)

        self.assertIn("종합 추천 2순위", summary)
        self.assertIn("품질 25.83/100", summary)
        self.assertIn("평균 11.998초", summary)
        self.assertIn("Intel Iris Xe", summary)
        self.assertIn("구조화 응답 재시도 0회", summary)
        self.assertIn("연구 15.55", summary)
        self.assertIn("리뷰 36.11", summary)
        self.assertIn("서지 66.67", summary)
        self.assertIn("서지정보 추가 요청 2회", summary)

    def test_unmeasured_model_is_labeled_without_an_invented_score(self):
        _, specs = load_model_catalog()
        gemma = next(spec for spec in specs if spec.model_id == "gemma3:4b")

        summary = model_benchmark_summary(gemma)

        self.assertEqual(summary, "실논문 벤치마크 미실시")
        self.assertNotIn("/100", summary)

    def test_qwen_4b_benchmark_summary_explains_third_place_strength(self):
        _, specs = load_model_catalog()
        qwen = next(spec for spec in specs if spec.model_id == "qwen3:4b")

        summary = model_benchmark_summary(qwen)

        self.assertIn("종합 추천 3순위", summary)
        self.assertIn("품질 80.8/100", summary)
        self.assertIn("평균 15.1초", summary)
        self.assertIn("수치 결과", summary)

    def test_qwen35_4b_summary_explains_accuracy_first_recommendation(self):
        _, specs = load_model_catalog()
        qwen = next(spec for spec in specs if spec.model_id == "qwen3.5:4b")

        summary = model_benchmark_summary(qwen)

        self.assertIn("종합 추천 1순위", summary)
        self.assertIn("품질 33.92/100", summary)
        self.assertIn("연구 18.61", summary)
        self.assertIn("리뷰 49.22", summary)
        self.assertIn("서지 87.5", summary)

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
            if url.endswith("/api/ps"):
                return {
                    "models": [
                        {
                            "name": "qwen3:4b",
                            "size": 2_500_000_000,
                            "size_vram": 2_500_000_000,
                        }
                    ]
                }
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
        self.assertEqual(status.running_models[0].processor, "GPU")

    def test_auto_profile_prefers_a_compatible_installed_model(self):
        recommendation = recommend_models(
            hardware(total_ram=32, available_ram=28),
            ollama("qwen3:4b"),
            profile="auto",
        )
        self.assertEqual(recommendation.recommended.spec.model_id, "qwen3:4b")
        self.assertTrue(recommendation.recommended.installed)

    def test_balanced_profile_prefers_top_ranked_real_paper_model(self):
        recommendation = recommend_models(
            hardware(total_ram=16, available_ram=14),
            ollama(),
            profile="balanced",
        )
        self.assertEqual(
            recommendation.recommended.spec.model_id,
            "qwen3.5:4b",
        )
        self.assertEqual(recommendation.recommended.rating, "권장")

    def test_speed_profile_prefers_faster_granite_alternative(self):
        recommendation = recommend_models(
            hardware(total_ram=16, available_ram=14),
            ollama(),
            profile="speed",
        )

        self.assertEqual(
            recommendation.recommended.spec.model_id,
            "granite4.1:3b",
        )

    def test_eight_gb_auto_profile_does_not_recommend_local_ai(self):
        recommendation = recommend_models(
            hardware(total_ram=8, available_ram=6),
            ollama(),
            profile="auto",
        )
        self.assertIsNone(recommendation.recommended)
        self.assertTrue(
            all(item.rating == "비권장" for item in recommendation.candidates)
        )

    def test_sixteen_gb_auto_profile_keeps_benchmarked_background_default(self):
        recommendation = recommend_models(
            hardware(total_ram=16, available_ram=8),
            ollama("granite4.1:3b"),
            profile="auto",
        )

        self.assertEqual(
            recommendation.recommended.spec.model_id,
            "qwen3:1.7b",
        )

    def test_memory_tier_guidance_explains_8_16_and_24_gb_policies(self):
        self.assertIn("지원하지 않습니다", memory_tier_guidance(8))
        self.assertIn("시스템 여유 0.5GB", memory_tier_guidance(16))
        self.assertIn("Qwen3 1.7B", memory_tier_guidance(16))
        self.assertIn("모델 크기를 제한하지 않고", memory_tier_guidance(24))

    def test_recommendation_overview_separates_background_manual_and_advanced(self):
        overview = recommendation_tier_overview()

        self.assertIn("백그라운드 서지·Abstract", overview)
        self.assertIn("수동 본문 분석 기본", overview)
        self.assertIn("속도 우선 대안", overview)
        self.assertIn("8B+", overview)
        self.assertIn("Granite 3.3 2B", overview)
        self.assertIn("Qwen3.5 2B", overview)
        self.assertIn("Qwen3.5 4B", overview)

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
            self.assertEqual(saved.recommended_model, "qwen3.5:4b")
            self.assertEqual(saved.model_catalog_version, "2026.08.01.2")
            self.assertEqual(saved.hardware_profile["cpu_model"], "Test CPU")
            self.assertEqual(
                saved.hardware_profile["recommendation_profile"], "quality"
            )
            self.assertEqual(
                assessment.recommendation.recommended.spec.model_id,
                "qwen3.5:4b",
            )


if __name__ == "__main__":
    unittest.main()
