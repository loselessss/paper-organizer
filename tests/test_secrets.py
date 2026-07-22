import os
import unittest
from unittest.mock import patch

from paper_organizer.infra.secrets import EnvironmentSecretStore


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


if __name__ == "__main__":
    unittest.main()
