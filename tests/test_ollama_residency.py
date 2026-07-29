import unittest

from paper_organizer.core.ollama_residency import (
    residency_description,
    resolve_ollama_keep_alive,
)


class OllamaResidencyTests(unittest.TestCase):
    def test_auto_keeps_four_b_model_for_thirty_minutes_on_32gb_pc(self):
        self.assertEqual(
            resolve_ollama_keep_alive(
                "auto",
                "qwen3:4b",
                "qwen3:4b",
                32,
            ),
            "30m",
        )

    def test_non_resident_model_is_unloaded_after_request(self):
        self.assertEqual(
            resolve_ollama_keep_alive(
                "always",
                "qwen3:4b",
                "qwen3:1.7b",
                32,
            ),
            0,
        )

    def test_low_memory_auto_mode_uses_conservative_default(self):
        self.assertEqual(
            resolve_ollama_keep_alive("auto", "qwen3:4b", "qwen3:4b", 16),
            "5m",
        )

    def test_description_explains_no_eager_loading_and_memory_cost(self):
        message = residency_description("always", "qwen3:4b", 32)
        self.assertIn("첫 요청 뒤", message)
        self.assertIn("RAM·VRAM", message)
        self.assertIn("미리 적재하지 않으며", message)


if __name__ == "__main__":
    unittest.main()
