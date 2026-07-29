import unittest

from paper_organizer.application.summary_preprocessing import (
    preprocess_paper_text,
    remove_figure_and_table_captions,
)


class SummaryPreprocessingTests(unittest.TestCase):
    def test_sections_remove_repeated_furniture_and_reference_tail(self):
        pages = [
            "Journal Header 2026\n1\nIntroduction\n"
            "This study asks whether salt changes recovery.\nJournal Footer",
            "Journal Header 2026\n2\nMaterials and Methods\n"
            "Cells were grown in de-\nfined medium at 16 C.\nJournal Footer",
            "Journal Header 2026\n3\nResults\n"
            "Recovery increased by 20 percent.\nJournal Footer",
            "Journal Header 2026\n4\nDiscussion\n"
            "The result supports the stated hypothesis.\nReferences\n"
            "Unrelated Author, 2020.\nJournal Footer",
        ]

        prepared = preprocess_paper_text(pages)

        self.assertEqual(
            [section.name for section in prepared.sections],
            ["introduction", "methods", "results", "discussion"],
        )
        self.assertNotIn("Journal Header", prepared.text)
        self.assertNotIn("Journal Footer", prepared.text)
        self.assertNotIn("Unrelated Author", prepared.text)
        self.assertIn("defined medium", prepared.text)
        self.assertIn("[PARAGRAPH 1]", prepared.text)
        self.assertEqual(prepared.included_pdf_pages, (1, 2, 3, 4))

    def test_ocr_noise_is_removed_without_destroying_technical_hyphens(self):
        pages = [
            "Introduction\nanti-CD3 was used @ @ after micro-\nscopic inspection.",
            "Results\nThe anti-CD3 signal remained stable |||.",
        ]

        prepared = preprocess_paper_text(pages)

        self.assertIn("anti-CD3", prepared.text)
        self.assertIn("microscopic", prepared.text)
        self.assertNotIn("@", prepared.text)
        self.assertNotIn("|||", prepared.text)

    def test_regex_candidates_are_labeled_before_section_context(self):
        pages = [
            "Introduction\nPublished in 2025. DOI 10.1234/example.7\n"
            "The research question is explicit.",
            "Results\nThe measured response was reproducible.",
        ]

        prepared = preprocess_paper_text(pages)

        self.assertEqual(
            prepared.regex_facts,
            ("DOI candidates: 10.1234/example.7", "Year candidates: 2025"),
        )
        self.assertTrue(prepared.text.startswith("[REGEX-VALIDATED CANDIDATES]"))

    def test_front_matter_preserves_title_authors_and_venue(self):
        pages = [
            "A Precise Paper Title\nMina Vale and Theo Karst\nSynthetic Journal\n"
            "Abstract\nThe study is summarized here.",
            "Introduction\nThe research question is explicit.",
        ]
        prepared = preprocess_paper_text(pages)
        self.assertEqual(prepared.sections[0].name, "front")
        self.assertIn("A Precise Paper Title", prepared.text)
        self.assertIn("Mina Vale and Theo Karst", prepared.text)

    def test_small_model_caption_filter_keeps_scientific_prose(self):
        filtered = remove_figure_and_table_captions(
            [
                "Results\nSignal increased by 20 percent.\n"
                "Figure 2. Microscopy panels and scale bars.\n"
                "Table 1: Full measurement matrix.\n"
                "This result supports the hypothesis."
            ]
        )

        self.assertNotIn("Figure 2", filtered[0])
        self.assertNotIn("Table 1", filtered[0])
        self.assertIn("Signal increased", filtered[0])
        self.assertIn("supports the hypothesis", filtered[0])

    def test_page_labels_are_removed_without_touching_section_numbers(self):
        prepared = preprocess_paper_text(
            [
                "A useful paper\nPage 1 of 3\nAbstract\nEvidence " * 20,
                "[PDF Page 2]\n2. Methods\nMethod evidence.\n- 2 -",
                "페이지 3\n3 / 3\n3. Results\nMeasured evidence.",
            ]
        )

        self.assertNotIn("Page 1 of 3", prepared.text)
        self.assertNotIn("[PDF Page 2]", prepared.text)
        self.assertNotIn("- 2 -", prepared.text)
        self.assertNotIn("페이지 3", prepared.text)
        self.assertNotIn("3 / 3", prepared.text)
        self.assertIn("Method evidence", prepared.text)
        self.assertIn("Measured evidence", prepared.text)


if __name__ == "__main__":
    unittest.main()
