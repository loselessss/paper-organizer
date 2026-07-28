import unittest

from tests.benchmark.tools.score_output import score_summary, token_overlap


class BenchmarkToolTests(unittest.TestCase):
    def test_token_overlap_is_case_and_whitespace_insensitive(self):
        self.assertEqual(
            token_overlap("DnaK ratio 0.62", "The DNAK   ratio was 0.62."),
            1.0,
        )

    def test_forbidden_claim_and_evidence_coverage_are_reported(self):
        truth = {
            "document_id": "synthetic",
            "methods": ["Ni-NTA chromatography"],
            "key_findings": ["yield decreased to 5.1 mg/L"],
            "numeric_findings": [],
            "critical_negations": ["ATP did not improve purity"],
            "forbidden_claims": ["ATP completely removed DnaK"],
        }
        score = score_summary(
            truth,
            "Ni-NTA chromatography was used. Yield decreased to 5.1 mg/L. "
            "ATP did not improve purity.",
        )
        self.assertEqual(score["covered_claims"], 3)
        self.assertEqual(score["forbidden_hits"], 0)
