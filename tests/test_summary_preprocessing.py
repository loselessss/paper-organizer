import unittest

from paper_organizer.application.summary_preprocessing import (
    preprocess_paper_text,
    remove_figure_and_table_captions,
    remove_publisher_proof_boilerplate,
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

    def test_regex_candidates_preserve_complete_quantitative_results(self):
        pages = [
            "Abstract\nThe complex was evaluated against the free enzyme.",
            "Materials and Methods\nCells were incubated at 37 °C for 24 h.",
            "Results\nThe scaffolded complex degraded 45.12% melanin after 4 hours, "
            "compared with 6.70% for laccase alone, a 6.73-fold increase.\n"
            "PHB production reached 14.2 g/L after 72 hours [19].",
            "References\nA cited culture produced 99.0 g/L after 10 hours.",
        ]

        prepared = preprocess_paper_text(pages)
        result_fact = next(
            fact
            for fact in prepared.regex_facts
            if fact.startswith("Quantitative result candidates")
        )

        self.assertIn("45.12%", result_fact)
        self.assertIn("compared with 6.70%", result_fact)
        self.assertIn("6.73-fold", result_fact)
        self.assertIn("14.2 g/L after 72 hours", result_fact)
        self.assertNotIn("37 °C", result_fact)
        self.assertNotIn("99.0 g/L", result_fact)

    def test_results_and_discussion_heading_is_detected_as_results(self):
        prepared = preprocess_paper_text(
            [
                "1. Introduction\nThe study question is stated.",
                "2. Materials and methods\nCells were prepared.",
                "3. Results and discussion\nThe treated group produced 4.5-fold "
                "more PHB than the control after 72 h.",
                "4. Conclusion\nThe treatment improved PHB production.",
            ]
        )

        self.assertEqual(
            [section.name for section in prepared.sections],
            ["introduction", "methods", "results", "conclusion"],
        )
        self.assertIn("4.5-fold more PHB", prepared.text)
        self.assertIn("4.5-fold more PHB", prepared.regex_facts[-1])

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

    def test_publisher_proof_filter_keeps_scientific_proof_terms(self):
        filtered = remove_publisher_proof_boilerplate(
            [
                "Journal Pre-proof\n"
                "Accepted manuscript\n"
                "Please cite this article as: A precise paper\n"
                "Abstract\nThis is a proof-of-concept experiment.\n"
                "The mathematical proof is provided in Appendix A."
            ]
        )[0]

        self.assertNotIn("Journal Pre-proof", filtered)
        self.assertNotIn("Accepted manuscript", filtered)
        self.assertNotIn("Please cite", filtered)
        self.assertIn("proof-of-concept", filtered)
        self.assertIn("mathematical proof", filtered)

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
