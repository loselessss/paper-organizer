import unittest

from paper_organizer.infra.redaction import REDACTED, redact_headers, redact_text


class RedactionTests(unittest.TestCase):
    def test_redacts_openai_and_anthropic_key_shapes(self):
        openai_key = "sk-" + "proj-example-secret-123456"
        anthropic_key = "sk-ant-" + "api-example-secret-123456"
        redacted = redact_text(f"openai={openai_key} anthropic={anthropic_key}")
        self.assertNotIn(openai_key, redacted)
        self.assertNotIn(anthropic_key, redacted)
        self.assertEqual(redacted.count(REDACTED), 2)

    def test_redacts_authorization_assignments(self):
        value = redact_text("Authorization: Bearer private-token")
        self.assertEqual(value, f"Authorization: Bearer {REDACTED}")

    def test_redacts_environment_assignments(self):
        value = redact_text("OPENAI_API_KEY=private-token")
        self.assertEqual(value, f"OPENAI_API_KEY={REDACTED}")

    def test_redacts_sensitive_headers_only(self):
        headers = redact_headers(
            {
                "Authorization": "Bearer private-token",
                "x-api-key": "private-key",
                "Content-Type": "application/json",
            }
        )
        self.assertEqual(headers["Authorization"], REDACTED)
        self.assertEqual(headers["x-api-key"], REDACTED)
        self.assertEqual(headers["Content-Type"], "application/json")


if __name__ == "__main__":
    unittest.main()
