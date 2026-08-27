import json
import tempfile
import unittest
from pathlib import Path

from paper_organizer.application.ai_settings import AiSettingsController


class MemorySecretStore:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, provider):
        return self.values.get(provider)

    def set(self, provider, secret):
        self.values[provider] = secret

    def delete(self, provider):
        self.values.pop(provider, None)


class RecoveringModelManager:
    def __init__(self):
        self.calls = 0

    def installed_models(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("Ollama에 연결할 수 없습니다.")
        return ("qwen3:1.7b", "qwen3:4b")


class AiSettingsControllerTests(unittest.TestCase):
    def test_saves_provider_preferences_without_api_key(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = MemorySecretStore()
            controller = AiSettingsController(store, path)
            view = controller.save_preferences(
                provider="openai",
                model="gpt-test",
                cloud_processing_consent=True,
                cloud_request_profile="high_throughput",
                cloud_max_parallel_requests=6,
                cloud_monthly_budget_usd=0,
                model_profile="quality",
                summary_language="source",
                summary_timeout_seconds=1500,
                automatic_analysis_interval_seconds=20,
                manual_analysis_interval_seconds=2,
                ollama_residency_mode="30m",
                ollama_resident_model="qwen3:4b",
            )
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(view.provider, "openai")
        self.assertEqual(view.model, "gpt-test")
        self.assertEqual(view.effective_parallel_requests, 6)
        self.assertIsNone(view.cloud_monthly_budget_usd)
        self.assertEqual(view.model_profile, "quality")
        self.assertEqual(view.summary_language, "source")
        self.assertEqual(view.summary_timeout_seconds, 1500)
        self.assertEqual(view.automatic_analysis_interval_seconds, 20)
        self.assertEqual(view.manual_analysis_interval_seconds, 2)
        self.assertEqual(view.ollama_residency_mode, "30m")
        self.assertEqual(view.ollama_resident_model, "qwen3:4b")
        self.assertEqual(saved["summary_timeout_seconds"], 1500)
        self.assertEqual(saved["automatic_analysis_interval_seconds"], 20)
        self.assertEqual(saved["manual_analysis_interval_seconds"], 2)
        self.assertEqual(saved["ollama_residency_mode"], "30m")
        self.assertNotIn("api_key", saved)

    def test_key_status_is_masked_and_key_can_be_deleted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            store = MemorySecretStore()
            controller = AiSettingsController(store, path)
            controller.save_preferences(
                provider="anthropic",
                model="claude-test",
                cloud_processing_consent=False,
                cloud_request_profile="conservative",
                cloud_max_parallel_requests=1,
                cloud_monthly_budget_usd=10,
            )
            view = controller.save_api_key("anthropic", "private-key-1234")
            status = controller.key_status("anthropic")
            deleted = controller.delete_api_key("anthropic")

        self.assertTrue(view.key_configured)
        self.assertEqual(status.masked_hint, "••••1234")
        self.assertFalse(hasattr(status, "secret"))
        self.assertFalse(deleted.key_configured)

    def test_rejects_anthropic_admin_key_before_storage(self):
        store = MemorySecretStore()
        controller = AiSettingsController(store, Path("unused.json"))
        admin_key = "sk-ant-" + "admin-example-secret"
        with self.assertRaisesRegex(ValueError, "Admin API keys"):
            controller.save_api_key("anthropic", admin_key)
        self.assertEqual(store.values, {})

    def test_provider_models_are_preserved_independently(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            controller = AiSettingsController(MemorySecretStore(), path)
            controller.save_preferences(
                provider="openai",
                model="gpt-custom",
                cloud_processing_consent=False,
                cloud_request_profile="conservative",
                cloud_max_parallel_requests=1,
                cloud_monthly_budget_usd=0,
            )
            controller.save_preferences(
                provider="anthropic",
                model="claude-custom",
                cloud_processing_consent=False,
                cloud_request_profile="conservative",
                cloud_max_parallel_requests=1,
                cloud_monthly_budget_usd=0,
            )
            openai_view = controller.save_preferences(
                provider="openai",
                model="gpt-custom",
                cloud_processing_consent=False,
                cloud_request_profile="conservative",
                cloud_max_parallel_requests=1,
                cloud_monthly_budget_usd=0,
            )

        self.assertEqual(openai_view.model, "gpt-custom")

    def test_verified_ollama_model_can_be_selected_and_saved_immediately(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            starts = []
            controller = AiSettingsController(
                MemorySecretStore(),
                path,
                ollama_starter=lambda: bool(starts.append(True) or True),
            )

            view = controller.select_ollama_model("qwen3:8b")
            controller.select_ollama_model("qwen3:8b")

            self.assertEqual(view.provider, "local")
            self.assertEqual(view.model, "qwen3:8b")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["selected_model"], "qwen3:8b")
            self.assertEqual(starts, [True])

    def test_saving_a_different_ollama_model_only_ensures_server_once(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            starts = []
            restarts = []
            controller = AiSettingsController(
                MemorySecretStore(),
                path,
                ollama_starter=lambda: bool(starts.append(True) or True),
                ollama_restarter=lambda: bool(restarts.append(True) or True),
            )
            controller.select_ollama_model("qwen3:1.7b")
            starts.clear()

            controller.save_preferences(
                provider="ollama",
                model="qwen3:4b",
                cloud_processing_consent=False,
                cloud_request_profile="conservative",
                cloud_max_parallel_requests=1,
                cloud_monthly_budget_usd=0,
            )

        self.assertEqual(starts, [True])
        self.assertEqual(restarts, [])

    def test_background_manual_and_residency_are_saved_together(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            starts = []
            controller = AiSettingsController(
                MemorySecretStore(),
                path,
                ollama_starter=lambda: bool(starts.append(True) or True),
            )

            view = controller.save_preferences(
                provider="ollama",
                model="qwen3:1.7b",
                background_model="qwen3:1.7b",
                manual_model="qwen3:4b",
                background_model_resident=True,
                cloud_processing_consent=False,
                cloud_request_profile="conservative",
                cloud_max_parallel_requests=1,
                cloud_monthly_budget_usd=0,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(view.background_model, "qwen3:1.7b")
        self.assertEqual(view.manual_model, "qwen3:4b")
        self.assertTrue(view.background_model_resident)
        self.assertEqual(saved["selected_model"], "qwen3:1.7b")
        self.assertEqual(saved["ollama_residency_mode"], "always")
        self.assertEqual(saved["ollama_resident_model"], "qwen3:1.7b")
        self.assertEqual(starts, [True])

    def test_manual_model_can_be_changed_without_replacing_background_model(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            controller = AiSettingsController(
                MemorySecretStore(),
                path,
                ollama_starter=lambda: True,
            )
            controller.select_ollama_model("qwen3:1.7b")

            view = controller.select_ollama_model(
                "qwen3:4b",
                purpose="manual",
            )

        self.assertEqual(view.background_model, "qwen3:1.7b")
        self.assertEqual(view.manual_model, "qwen3:4b")

    def test_gpu_priority_is_applied_and_persisted_on_save(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            changes = []
            controller = AiSettingsController(
                MemorySecretStore(),
                path,
                ollama_igpu_configurer=changes.append,
                ollama_starter=lambda: True,
                ollama_restarter=lambda: True,
            )

            view = controller.save_preferences(
                provider="ollama",
                model="qwen3:1.7b",
                cloud_processing_consent=False,
                cloud_request_profile="conservative",
                cloud_max_parallel_requests=1,
                cloud_monthly_budget_usd=0,
                ollama_force_igpu=True,
            )
            controller.save_preferences(
                provider="ollama",
                model="qwen3:1.7b",
                cloud_processing_consent=False,
                cloud_request_profile="conservative",
                cloud_max_parallel_requests=1,
                cloud_monthly_budget_usd=0,
                ollama_force_igpu=True,
            )
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(view.ollama_force_igpu)
        self.assertTrue(saved["ollama_force_igpu"])
        self.assertEqual(changes, [])

    def test_saved_gpu_priority_is_reapplied_on_app_start(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            changes = []
            controller = AiSettingsController(
                MemorySecretStore(),
                path,
                ollama_igpu_configurer=changes.append,
                ollama_starter=lambda: True,
                ollama_restarter=lambda: True,
            )
            controller.save_preferences(
                provider="ollama",
                model="qwen3:1.7b",
                cloud_processing_consent=False,
                cloud_request_profile="conservative",
                cloud_max_parallel_requests=1,
                cloud_monthly_budget_usd=0,
                ollama_force_igpu=True,
            )
            changes.clear()

            controller.synchronize_ollama_acceleration()

        self.assertEqual(changes, [])

    def test_ollama_restart_uses_injected_restarter(self):
        calls = []
        controller = AiSettingsController(
            MemorySecretStore(),
            Path("unused.json"),
            ollama_restarter=lambda: bool(calls.append(True) or True),
        )

        self.assertTrue(controller.restart_ollama_runtime())
        self.assertEqual(calls, [True])

    def test_ollama_start_uses_injected_starter(self):
        calls = []
        controller = AiSettingsController(
            MemorySecretStore(),
            Path("unused.json"),
            ollama_starter=lambda: bool(calls.append(True) or True),
        )

        self.assertTrue(controller.start_ollama_runtime())
        self.assertEqual(calls, [True])

    def test_installed_models_start_stopped_ollama_and_retry(self):
        manager = RecoveringModelManager()
        starts = []
        controller = AiSettingsController(
            MemorySecretStore(),
            Path("unused.json"),
            model_manager=manager,
            ollama_starter=lambda: starts.append(True) or True,
        )

        models = controller.installed_ollama_models()

        self.assertEqual(models, ("qwen3:1.7b", "qwen3:4b"))
        self.assertEqual(starts, [True])
        self.assertEqual(manager.calls, 2)

    def test_installed_models_report_runtime_start_failure(self):
        manager = RecoveringModelManager()
        controller = AiSettingsController(
            MemorySecretStore(),
            Path("unused.json"),
            model_manager=manager,
            ollama_starter=lambda: False,
        )

        with self.assertRaisesRegex(RuntimeError, "서버를 시작할 수 없습니다"):
            controller.installed_ollama_models()

        self.assertEqual(manager.calls, 1)

    def test_ollama_retirement_notice_is_needed_once_for_legacy_users(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            from paper_organizer.infra.settings import AppSettings, save_settings

            save_settings(
                AppSettings(
                    summary_provider="ollama",
                    managed_ollama_models=["qwen3:4b"],
                    ollama_resident_model="qwen3:4b",
                ),
                path,
            )
            controller = AiSettingsController(MemorySecretStore(), path)

            self.assertTrue(controller.should_show_ollama_retirement_notice())
            controller.acknowledge_ollama_retirement_notice()

            self.assertFalse(controller.should_show_ollama_retirement_notice())
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["summary_provider"], "local")
            self.assertTrue(saved["ollama_retirement_notice_acknowledged"])


if __name__ == "__main__":
    unittest.main()
