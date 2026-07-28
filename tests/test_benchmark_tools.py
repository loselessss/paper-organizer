import json
import unittest
from pathlib import Path

from tests.benchmark.tools.run_models import DEFAULT_MODELS
from tests.benchmark.tools.score_output import score_summary, token_overlap


class BenchmarkToolTests(unittest.TestCase):
    def test_cross_family_candidates_are_in_default_model_matrix(self):
        self.assertIn("phi4-mini", DEFAULT_MODELS)
        self.assertIn("gemma3:4b-it-qat", DEFAULT_MODELS)
        self.assertIn(
            "ministral-3:3b-instruct-2512-q4_K_M",
            DEFAULT_MODELS,
        )

    def test_token_overlap_is_case_and_whitespace_insensitive(self):
        self.assertEqual(
            token_overlap("DnaK ratio 0.62", "The DNAK   ratio was 0.62."),
            1.0,
        )

    def test_forbidden_claim_and_evidence_coverage_are_reported(self):
        truth = {
            "document_id": "synthetic",
            "title": "Salt improves protein purification",
            "research_question": "Does salt improve protein purification?",
            "methods": ["Ni-NTA chromatography"],
            "key_findings": ["yield decreased to 5.1 mg/L"],
            "numeric_findings": ["yield was 5.1 mg/L"],
            "conclusion": "Salt improved purity with a modest yield cost",
            "critical_negations": ["ATP did not improve purity"],
            "forbidden_claims": ["ATP completely removed DnaK"],
        }
        score = score_summary(
            truth,
            "Salt improves protein purification. The study asks whether salt "
            "improves protein purification. Ni-NTA chromatography was used. "
            "Yield decreased to 5.1 mg/L. The yield was 5.1 mg/L. ATP did not "
            "improve purity. Salt improved purity with a modest yield cost.",
        )
        self.assertEqual(score["score_100"], 100.0)
        self.assertEqual(score["covered_claims"], 4)
        self.assertEqual(score["forbidden_hits"], 0)

    def test_forbidden_claim_deducts_fifteen_points(self):
        truth = {
            "document_id": "synthetic",
            "title": "A complete benchmark title",
            "research_question": "Does the treatment improve yield?",
            "methods": ["Ni-NTA chromatography"],
            "key_findings": ["Yield improved to 5.1 mg/L"],
            "numeric_findings": ["Yield was 5.1 mg/L"],
            "critical_negations": ["ATP did not improve purity"],
            "conclusion": "The treatment improved yield",
            "forbidden_claims": ["ATP completely removed DnaK"],
        }
        output = " ".join(
            [
                truth["title"],
                truth["research_question"],
                *truth["methods"],
                *truth["key_findings"],
                *truth["numeric_findings"],
                *truth["critical_negations"],
                truth["conclusion"],
                truth["forbidden_claims"][0],
            ]
        )
        score = score_summary(truth, output)
        self.assertEqual(score["raw_points"], 100.0)
        self.assertEqual(score["penalty_points"], 15.0)
        self.assertEqual(score["score_100"], 85.0)

    def test_every_answer_key_can_receive_full_credit_without_false_penalty(self):
        root = Path(__file__).parent / "benchmark"
        manifest = json.loads(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        fields = (
            "title",
            "research_question",
            "methods",
            "key_findings",
            "numeric_findings",
            "critical_negations",
            "conclusion",
        )
        for document in manifest["documents"]:
            truth = json.loads(
                (root / document["ground_truth"]).read_text(encoding="utf-8")
            )
            parts = []
            for field in fields:
                value = truth[field]
                parts.extend(value if isinstance(value, list) else [value])
            with self.subTest(document_id=document["document_id"]):
                score = score_summary(truth, " | ".join(parts))
                self.assertEqual(score["score_100"], 100.0)
                self.assertEqual(score["forbidden_hits"], 0)
