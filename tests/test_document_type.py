"""Tests for strict patent and academic subtype classification."""

import unittest

from paper_organizer.core.document_type import classify_document_type


class DocumentTypeTests(unittest.TestCase):
    def test_us_patent_requires_heading_and_number(self) -> None:
        text = "United States Patent Application Publication\nUS 2026/0123456 A1\n(54) DEVICE"
        self.assertEqual(classify_document_type([text]).document_type, "patent")

    def test_pct_and_korean_formats_are_patents(self) -> None:
        pct = "World Intellectual Property Organization\nWO 2026/123456 A1\n(54) DEVICE"
        korean = "대한민국특허청\n공개특허공보 10-2026-0012345\n(54) 장치"
        self.assertEqual(classify_document_type([pct]).patent_office, "WIPO")
        self.assertEqual(classify_document_type([korean]).patent_office, "KR")

    def test_paper_discussing_patents_and_claims_is_not_patent(self) -> None:
        text = "Research Article\nAbstract\nWe study patent claims and inventors.\nIntroduction"
        self.assertEqual(classify_document_type([text]).document_type, "research_paper")

    def test_review_is_marked_before_summarization(self) -> None:
        text = "A systematic review and meta-analysis\nAbstract\nThis review synthesizes evidence."
        self.assertEqual(classify_document_type([text]).document_type, "review_paper")


if __name__ == "__main__":
    unittest.main()
