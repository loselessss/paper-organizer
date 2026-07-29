import tempfile
import unittest
from pathlib import Path

import fitz

from paper_organizer.application.library_workflow import (
    _default_metadata,
    _repair_title_text,
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


if __name__ == "__main__":
    unittest.main()
