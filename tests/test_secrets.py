import os
import unittest
from unittest.mock import patch

from paper_organizer.infra.secrets import (
    EnvironmentSecretStore,
    get_secret_status,
    mask_secret,
    sanitized_child_environment,
)


class SecretStoreTests(unittest.TestCase):
    def test_environment_store_reads_known_provider(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": " test-key "}):
            self.assertEqual(EnvironmentSecretStore().get("openai"), "test-key")

    def test_environment_store_does_not_write_process_environment(self):
        with self.assertRaises(RuntimeError):
            EnvironmentSecretStore().set("anthropic", "secret")

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            EnvironmentSecretStore().get("unknown")

    def test_mask_only_reveals_last_four_characters(self):
        self.assertEqual(mask_secret("a-long-secret-1234"), "••••1234")
        self.assertEqual(mask_secret(""), "")

    def test_secret_status_never_returns_the_full_key(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "private-secret-9876"}):
            status = get_secret_status(EnvironmentSecretStore(), "anthropic")
        self.assertTrue(status.configured)
        self.assertEqual(status.masked_hint, "••••9876")
        self.assertFalse(hasattr(status, "secret"))

    def test_child_environment_removes_api_keys(self):
        child_environment = sanitized_child_environment(
            {
                "OPENAI_API_KEY": "openai-secret",
                "ANTHROPIC_API_KEY": "anthropic-secret",
                "PATH": "safe-path",
            }
        )
        self.assertNotIn("OPENAI_API_KEY", child_environment)
        self.assertNotIn("ANTHROPIC_API_KEY", child_environment)
        self.assertEqual(child_environment["PATH"], "safe-path")


if __name__ == "__main__":
    unittest.main()
