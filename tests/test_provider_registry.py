import unittest

from paper_organizer.infra.settings import AppSettings
from paper_organizer.providers.registry import build_provider


class MemorySecretStore:
    def get(self, provider):
        return f"{provider}-secret"


class ProviderRegistryTests(unittest.TestCase):
    def test_configured_summary_timeout_reaches_every_provider(self):
        store = MemorySecretStore()
        for provider in ("local", "ollama", "openai", "anthropic"):
            with self.subTest(provider=provider):
                settings = AppSettings(
                    summary_provider=provider,
                    selected_model="qwen3:4b",
                    summary_timeout_seconds=1500,
                )
                configured = build_provider(settings, store)
                self.assertEqual(configured._timeout_seconds, 1500)

    def test_ollama_residency_policy_reaches_provider(self):
        settings = AppSettings(
            summary_provider="ollama",
            selected_model="qwen3:4b",
            ollama_residency_mode="auto",
            ollama_resident_model="qwen3:4b",
            hardware_profile={"memory_total_gb": 32},
        )

        configured = build_provider(settings, MemorySecretStore())

        self.assertEqual(configured._keep_alive, "30m")


if __name__ == "__main__":
    unittest.main()
