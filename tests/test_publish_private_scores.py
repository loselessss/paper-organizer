import json
import tempfile
import unittest
from pathlib import Path

from tests.benchmark.tools.publish_private_scores import (
    publish,
    sanitized_runs,
)


class PublishPrivateScoresTests(unittest.TestCase):
    def test_private_results_are_sanitized_before_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = root / "results" / "qwen3_1.7b" / "REAL-001.json"
            result.parent.mkdir(parents=True)
            result.write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "document_id": "REAL-001",
                        "difficulty": "research",
                        "title": "Private title",
                        "model": "qwen3:1.7b",
                        "elapsed_seconds": 12.5,
                        "processor": "GPU",
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "output": {"summary": "Private model output"},
                        "score": {
                            "score_100": 80,
                            "forbidden_hits": 0,
                            "bibliography": {"score_100": 75},
                        },
                    }
                ),
                encoding="utf-8",
            )

            runs = sanitized_runs(root / "results")
            output = publish(
                root / "results",
                root / "history.json",
                hardware_label="Test GPU / RAM 16GB",
            )
            text = output.read_text(encoding="utf-8")

        self.assertEqual(runs[0]["mean_score_100"], 80)
        self.assertEqual(runs[0]["research_mean_score_100"], 80)
        self.assertIsNone(runs[0]["review_mean_score_100"])
        self.assertIn("REAL-001", text)
        self.assertNotIn("Private title", text)
        self.assertNotIn("Private model output", text)
        self.assertNotIn(str(root), text)


if __name__ == "__main__":
    unittest.main()
