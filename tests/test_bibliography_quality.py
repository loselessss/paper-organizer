import unittest

from paper_organizer.application.bibliography_quality import (
    assess_bibliography_quality,
    bibliography_quality_summary,
)


class BibliographyQualityTests(unittest.TestCase):
    def test_external_verified_record_reports_crossref_sources(self):
        record = {
            "document": {"type": "research_paper"},
            "bibliography": {
                "title": "A useful enzyme paper",
                "authors": ["A. Researcher", "B. Scientist"],
                "year": 2026,
                "venue": "Journal of Useful Enzymes",
            },
            "curation": {
                "field_sources": {
                    "bibliography.title": "verified:crossref",
                    "bibliography.authors": "verified:crossref",
                    "bibliography.year": "verified:crossref",
                    "bibliography.venue": "verified:crossref",
                }
            },
        }

        quality = assess_bibliography_quality(record)

        self.assertEqual(quality.level, "external_verified")
        self.assertEqual(quality.label, "외부 검증됨")
        self.assertEqual(quality.issues, ())
        self.assertIn("저널/학회 Crossref", bibliography_quality_summary(record))

    def test_suspicious_values_request_manual_review(self):
        record = {
            "document": {"type": "research_paper"},
            "bibliography": {
                "title": "Research Article",
                "authors": ["Last date of publication"],
                "year": 2017,
                "venue": "ResearchGate",
            },
            "curation": {
                "field_sources": {
                    "bibliography.title": "auto:regex",
                    "bibliography.authors": "ai:ollama",
                    "bibliography.year": "auto:regex",
                    "bibliography.venue": "ai:ollama",
                }
            },
        }

        quality = assess_bibliography_quality(record)

        self.assertEqual(quality.level, "needs_review")
        self.assertIn("제목이 일반 머리말", quality.issues)
        self.assertIn("저자 값 의심", quality.issues)
        self.assertIn("저널/학회가 배포 플랫폼", quality.issues)

    def test_user_owned_clean_record_is_certain(self):
        record = {
            "document": {"type": "research_paper"},
            "bibliography": {
                "title": "Curated paper",
                "authors": ["Curator One", "Curator Two"],
                "year": 2024,
                "venue": "Curated Journal",
            },
            "curation": {
                "field_sources": {
                    "bibliography.title": "user",
                    "bibliography.authors": "user",
                    "bibliography.year": "user",
                    "bibliography.venue": "user",
                }
            },
        }

        quality = assess_bibliography_quality(record)

        self.assertEqual(quality.level, "certain")
        self.assertEqual(quality.label, "확실")


if __name__ == "__main__":
    unittest.main()
