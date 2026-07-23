import unittest

from paper_organizer.core.classifier import (
    DEFAULT_CATEGORY,
    classify_text,
    extract_venue,
    load_taxonomy,
    taxonomy_category_names,
)


PROTEIN_PAGES = [
    "Directed evolution of a thermostable enzyme scaffold\n"
    "Abstract We report protein engineering of an enzyme using directed evolution "
    "and rational design. Mutagenesis libraries were screened for protein stability.",
    "Methods Recombinant protein expression in Escherichia coli was followed by "
    "chromatography purification. Catalytic efficiency and kcat were measured for "
    "each enzyme variant produced by directed evolution.",
]

ML_PAGES = [
    "A transformer architecture for image classification\n"
    "Abstract We train a deep learning model on a large dataset and report "
    "benchmark accuracy against convolutional baselines.",
    "Methods The neural network was trained on 8 GPUs. Fine-tuning improved "
    "accuracy on object detection and segmentation benchmarks.",
]


class ClassifierTests(unittest.TestCase):
    def test_bundled_taxonomy_uses_department_level_categories(self):
        names = taxonomy_category_names(load_taxonomy())
        self.assertIn("생물공학", names)
        self.assertIn("컴퓨터공학", names)
        self.assertGreaterEqual(len(names), 15)
        self.assertEqual(len(names), len(set(names)))

    def test_protein_engineering_paper_is_classified_with_subcategory(self):
        result = classify_text(
            "Directed evolution of a thermostable enzyme scaffold", PROTEIN_PAGES
        )
        self.assertEqual(result.category, "생물공학")
        self.assertEqual(result.subcategory, "단백질공학")
        self.assertTrue(result.classified)
        self.assertGreater(result.confidence, 0.5)
        self.assertIn("protein engineering", result.matched_keywords)

    def test_machine_learning_paper_is_classified(self):
        result = classify_text(
            "A transformer architecture for image classification", ML_PAGES
        )
        self.assertEqual(result.category, "컴퓨터공학")
        self.assertIn(result.subcategory, {"인공지능·기계학습", "컴퓨터비전"})

    def test_allowed_categories_limits_the_candidates(self):
        result = classify_text(
            "A transformer architecture for image classification",
            ML_PAGES,
            allowed_categories=["생물공학", "의학"],
        )
        self.assertEqual(result.category, DEFAULT_CATEGORY)

    def test_unrelated_text_stays_uncategorized(self):
        result = classify_text("Weekend trip notes", ["소풍 준비물과 간식 목록입니다."])
        self.assertEqual(result.category, DEFAULT_CATEGORY)
        self.assertFalse(result.classified)

    def test_venue_is_extracted_from_the_header_line(self):
        pages = [
            "Journal of Molecular Biology 431 (2019) 1121-1134\n"
            "Directed evolution of a thermostable enzyme\n"
            "John Doe, Jane Roe\nUniversity of Somewhere",
        ]
        self.assertEqual(extract_venue(pages), "Journal of Molecular Biology")

    def test_venue_ignores_affiliation_and_download_banners(self):
        pages = [
            "Downloaded from www.example.org\n"
            "Department of Chemistry, University of Somewhere\n"
            "See discussions, stats, and author profiles at ResearchGate",
        ]
        self.assertEqual(extract_venue(pages), "")


if __name__ == "__main__":
    unittest.main()
