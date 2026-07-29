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
        self.assertEqual(view.ollama_residency_mode, "30m")
        self.assertEqual(view.ollama_resident_model, "qwen3:4b")
        self.assertEqual(saved["summary_timeout_seconds"], 1500)
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
            controller = AiSettingsController(MemorySecretStore(), path)

            view = controller.select_ollama_model("qwen3:8b")

            self.assertEqual(view.provider, "ollama")
            self.assertEqual(view.model, "qwen3:8b")
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["selected_model"], "qwen3:8b")

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

        with self.assertRaisesRegex(RuntimeError, "시작 메뉴에서 Ollama를 실행"):
            controller.installed_ollama_models()

        self.assertEqual(manager.calls, 1)


if __name__ == "__main__":
    unittest.main()
