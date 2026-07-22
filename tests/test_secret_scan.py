import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_secrets import find_potential_secrets


class SecretScanTests(unittest.TestCase):
    def test_finds_key_without_returning_secret_value(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            secret = b"sk-" + b"exampleSecretValue1234567890"
            source = root / "source.txt"
            source.write_bytes(b"token=" + secret + b"\n")
            with patch(
                "scripts.check_secrets.tracked_files", return_value=[source]
            ):
                findings = find_potential_secrets(root)

        self.assertEqual(findings, [(Path("source.txt"), 1, "OpenAI API key")])
        self.assertNotIn(secret.decode(), repr(findings))

    def test_ignores_non_secret_documentation_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.txt"
            source.write_text("Use an sk- key", encoding="utf-8")
            with patch(
                "scripts.check_secrets.tracked_files", return_value=[source]
            ):
                self.assertEqual(find_potential_secrets(root), [])


if __name__ == "__main__":
    unittest.main()
