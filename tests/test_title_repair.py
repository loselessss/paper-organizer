import tempfile
import unittest
from pathlib import Path

import fitz

from paper_organizer.application.library_workflow import (
    _default_metadata,
    _repair_title_text,
    _repeated_page_lines,
)


def write_title_pdf(
    path: Path,
    *,
    metadata_title: str,
    page_title: str,
    title_font_size: float = 26,
) -> list[str]:
    document = fitz.open()
    page = document.new_page()
    # Insert body first to reproduce PDFs whose extraction order does not follow
    # the visual reading order.
    page.insert_text(
        (50, 260),
        "Postgenome research body text appears before the visual title.",
        fontsize=9,
    )
    page.insert_text((50, 125), page_title, fontsize=title_font_size)
    document.set_metadata({"title": metadata_title})
    document.save(path)
    document.close()

    reopened = fitz.open(path)
    try:
        return [page.get_text() for page in reopened]
    finally:
        reopened.close()


class TitleRepairTests(unittest.TestCase):
    def test_visual_byline_replaces_incomplete_pdf_author_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "full-byline.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((50, 110), "Quantifying circulating cell-free", fontsize=22)
            page.insert_text((50, 138), "DNA in humans", fontsize=22)
            page.insert_text(
                (50, 175),
                "Romain Meddeb1,2, Zahra Al Amir Dache1,2 & Alain R. Thierry1,2",
                fontsize=10,
            )
            page.insert_text(
                (50, 215),
                "To our knowledge this study examines circulating DNA variability.",
                fontsize=9,
            )
            document.set_metadata(
                {
                    "title": "Quantifying circulating cell-free DNA in humans",
                    "author": "Romain Meddeb",
                }
            )
            document.save(path)
            document.close()
            reopened = fitz.open(path)
            try:
                pages = [page.get_text() for page in reopened]
            finally:
                reopened.close()

            metadata = _default_metadata(path, pages)

        self.assertEqual(
            metadata.authors,
            ["Romain Meddeb", "Zahra Al Amir Dache", "Alain R. Thierry"],
        )

    def test_title_repeated_on_wrapper_and_article_page_is_not_a_running_header(self):
        title = "Effective Melanin Degradation by an Enzyme Complex"
        pages = [
            "ResearchGate\nSee discussions, stats, and author profiles for this publication\n"
            f"Citations 12 Reads 300\n{title}",
            f"{title}\nAuthors\nAbstract\nThis study examines melanin degradation. "
            + "Experimental details and results are described here. " * 12,
            "Methods\nThe enzyme complex was purified and tested. " * 15,
        ]

        repeated = _repeated_page_lines(pages)

        self.assertNotIn(title.casefold(), repeated)

    def test_repeated_running_header_is_not_used_as_title(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "running-header.pdf"
            document = fitz.open()
            for page_number in range(3):
                page = document.new_page()
                page.insert_text((50, 70), "JOURNAL RUNNING HEADER", fontsize=20)
                if page_number == 0:
                    page.insert_text(
                        (50, 120),
                        "A Reliable Method for Protein Folding",
                        fontsize=18,
                    )
                    page.insert_text(
                        (50, 145),
                        "under Industrial Conditions",
                        fontsize=18,
                    )
                page.insert_text(
                    (50, 240),
                    "Abstract and body text for font-size estimation.",
                    fontsize=9,
                )
            document.save(path)
            document.close()
            reopened = fitz.open(path)
            try:
                pages = [page.get_text() for page in reopened]
            finally:
                reopened.close()

            metadata = _default_metadata(path, pages)

        self.assertEqual(
            metadata.title,
            "A Reliable Method for Protein Folding under Industrial Conditions",
        )

    def test_visual_title_keeps_a_wrapped_second_line(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "wrapped-title.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((50, 115), "Effective Melanin Degradation by a", fontsize=18)
            page.insert_text(
                (50, 140),
                "Synergistic Laccase-Peroxidase Enzyme Complex",
                fontsize=18,
            )
            page.insert_text((50, 175), "Jane Kim and John Lee", fontsize=11)
            page.insert_text((50, 230), "Abstract This study examines enzymes.", fontsize=9)
            document.set_metadata(
                {"title": "Effective Melanin Degradation by a"}
            )
            document.save(path)
            document.close()
            reopened = fitz.open(path)
            try:
                pages = [page.get_text() for page in reopened]
            finally:
                reopened.close()

            metadata = _default_metadata(path, pages)

        self.assertEqual(
            metadata.title,
            "Effective Melanin Degradation by a "
            "Synergistic Laccase-Peroxidase Enzyme Complex",
        )

    def test_broken_korean_metadata_uses_visual_page_title(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Chaperon vectors I.pdf"
            pages = write_title_pdf(
                path,
                metadata_title="24È£_ÃÖÁ¾_",
                page_title="Chaperone Plasmid Set",
            )

            metadata = _default_metadata(path, pages)

        self.assertEqual(metadata.title, "Chaperone Plasmid Set")

    def test_common_utf8_and_korean_mojibake_is_repaired(self):
        self.assertEqual(
            _repair_title_text("FranÃ§ais Protein Folding"),
            "Français Protein Folding",
        )
        self.assertEqual(_repair_title_text("24È£_ÃÖÁ¾_"), "24호_최종_")

    def test_valid_metadata_keeps_priority_over_visual_heading(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.pdf"
            pages = write_title_pdf(
                path,
                metadata_title="Authoritative Metadata Title",
                page_title="Displayed Running Heading",
            )

            metadata = _default_metadata(path, pages)

        self.assertEqual(metadata.title, "Authoritative Metadata Title")

    def test_filename_shaped_metadata_does_not_override_visual_title(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "The_Stability_Improvement_of_a-Amylase_Enzyme_from.pdf"
            pages = write_title_pdf(
                path,
                metadata_title="The_Stability_Improvement_of_a-Amylase_Enzyme_from",
                page_title="The Stability Improvement of Amylase Enzyme",
                title_font_size=18,
            )

            metadata = _default_metadata(path, pages)

        self.assertEqual(
            metadata.title,
            "The Stability Improvement of Amylase Enzyme",
        )


if __name__ == "__main__":
    unittest.main()
