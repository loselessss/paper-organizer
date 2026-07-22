import unittest

from paper_organizer.core.document_identity import (
    build_identity_from_pages,
    compare_identities,
    detect_wrapper_pages,
)
from paper_organizer.models.paper import DuplicateKind


def paper_pages(ending: str = "The method improves accuracy and robustness.") -> list[str]:
    return [
        (
            "A Reliable Method for Cell Analysis\n"
            "A. Researcher, B. Researcher\n"
            "Abstract\nThis study presents a reliable method for cell analysis. " * 8
        ),
        (
            "Introduction\nThe experiment uses cultured A549 cells and controlled "
            "conditions. Methods and materials are described in detail. " * 12
        ),
        (
            "Methods\nA549 cells were cultured in DMEM with 10% FBS. "
            "Measurements were repeated three times under identical conditions. " * 12
        ),
        (
            "Results\nThe proposed method improved the primary outcome on every "
            "evaluation dataset. Statistical analysis confirmed the result. " * 12
        ),
        ("Conclusion\n" + (ending + " ") * 20 + "\nReferences\n[1] Example reference."),
    ]


class DocumentIdentityTests(unittest.TestCase):
    def test_researchgate_cover_is_ignored_for_work_identity(self):
        cover = (
            "ResearchGate\nSee discussions, stats, and author profiles for this publication "
            "at researchgate.net/publication/123\nCitations 10 Reads 300"
        )
        original = build_identity_from_pages("a" * 64, paper_pages())
        wrapped = build_identity_from_pages("b" * 64, [cover, *paper_pages()])
        self.assertEqual(wrapped.source_variant, "researchgate")
        self.assertEqual(wrapped.content_start_pdf_page, 2)
        self.assertEqual(original.content_fingerprint, wrapped.content_fingerprint)
        self.assertEqual(
            compare_identities(original, wrapped).kind, DuplicateKind.SAME_WORK
        )

    def test_normal_first_page_is_not_treated_as_wrapper(self):
        self.assertEqual(detect_wrapper_pages(paper_pages()), ())

    def test_identical_file_hash_is_exact_duplicate(self):
        left = build_identity_from_pages("a" * 64, paper_pages())
        right = build_identity_from_pages("a" * 64, paper_pages())
        self.assertEqual(compare_identities(left, right).kind, DuplicateKind.EXACT_FILE)

    def test_changed_results_are_not_automatically_same_work(self):
        original = build_identity_from_pages("a" * 64, paper_pages())
        changed = build_identity_from_pages(
            "b" * 64,
            paper_pages(
                "A different intervention failed and produced opposite conclusions across all cohorts."
            ),
        )
        self.assertNotEqual(
            compare_identities(original, changed).kind, DuplicateKind.SAME_WORK
        )


if __name__ == "__main__":
    unittest.main()
