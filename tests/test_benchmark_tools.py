import json
import unittest
from pathlib import Path

from tests.benchmark.tools.run_models import (
    DEFAULT_MODELS,
    PRIVATE_RUNNER,
    _documents,
    apply_benchmark_acceleration,
    private_benchmark_command,
)
from paper_organizer.infra.ollama_acceleration import OLLAMA_IGPU_ENVIRONMENT
from paper_organizer.infra.settings import AppSettings
from tests.benchmark.tools.score_output import (
    score_bibliography,
    score_summary,
    token_overlap,
)


class BenchmarkToolTests(unittest.TestCase):
    def test_benchmark_uses_saved_gpu_priority_and_can_remove_it(self):
        environment = {}

        label = apply_benchmark_acceleration(AppSettings(), environment)

        self.assertIn("GPU 우선", label)
        self.assertEqual(environment[OLLAMA_IGPU_ENVIRONMENT], "1")

        apply_benchmark_acceleration(
            AppSettings(ollama_force_igpu=False),
            environment,
        )
        self.assertNotIn(OLLAMA_IGPU_ENVIRONMENT, environment)

    def test_default_benchmark_command_targets_private_papers(self):
        command = private_benchmark_command(
            ["qwen3:0.6b"],
            language="source",
            resume=True,
        )

        self.assertEqual(Path(command[1]), PRIVATE_RUNNER)
        self.assertIn("qwen3:0.6b", command)
        self.assertIn("--resume", command)

    def test_bibliography_score_checks_title_authors_year_and_venue(self):
        truth = {
            "title": "Exact Paper Title",
            "authors": ["Mina Vale", "Theo Karst"],
            "year": "2026",
            "venue": "Journal of Synthetic Results",
        }
        output = json.dumps(
            {
                "title": "Exact Paper Title",
                "authors": ["Mina Vale", "Theo Karst"],
                "year": "2026",
                "venue": "ResearchGate",
            }
        )

        score = score_bibliography(truth, output)

        self.assertEqual(score["score_100"], 75.0)
        self.assertFalse(score["field_matches"]["venue"])
        self.assertTrue(score["field_matches"]["authors"])

    def test_cross_family_candidates_are_in_default_model_matrix(self):
        self.assertIn("granite3.3:2b", DEFAULT_MODELS)
        self.assertIn("phi4-mini", DEFAULT_MODELS)
        self.assertIn("gemma3:4b-it-qat", DEFAULT_MODELS)
        self.assertIn(
            "ministral-3:3b-instruct-2512-q4_K_M",
            DEFAULT_MODELS,
        )

    def test_model_benchmark_excludes_ocr_documents(self):
        documents = _documents(set())

        self.assertEqual(len(documents), 6)
        self.assertFalse(
            any(
                str(document.get("difficulty", "")).startswith("ocr_")
                for document in documents
            )
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
