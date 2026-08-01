"""Tests for strict patent and academic subtype classification."""

import unittest

from paper_organizer.core.document_type import (
    classify_document_type,
    detect_document_bundle,
)


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

    def test_review_of_phrase_in_title_is_marked_as_review(self) -> None:
        text = (
            "Food exposure assessment: a review of hazard characterisation\n"
            "Abstract\nThis paper reviews evidence for a public-health assessment."
        )

        self.assertEqual(classify_document_type([text]).document_type, "review_paper")

    def test_two_korean_patent_title_pages_are_a_bundle(self) -> None:
        first = (
            "(19) 대한민국특허청(KR)\n(12) 등록특허공보(B1)\n"
            "(11) 등록번호 10-2052132\n(54) 발명의 명칭 첫 번째 발명"
        )
        second = (
            "(19) 대한민국특허청(KR)\n(12) 등록특허공보(B1)\n"
            "(11) 등록번호 10-1717214\n(54) 발명의 명칭 두 번째 발명"
        )

        decision = detect_document_bundle([first, "본문", second])

        self.assertTrue(decision.is_multiple)
        self.assertEqual(decision.document_count, 2)
        self.assertEqual(decision.identifiers, ("10-2052132", "10-1717214"))

    def test_one_patent_with_application_and_publication_numbers_is_not_bundle(self) -> None:
        page = (
            "(19) 대한민국특허청(KR)\n(12) 등록특허공보(B1)\n"
            "(11) 등록번호 10-2052132\n(21) 출원번호 10-2017-0092335\n"
            "(65) 공개번호 10-2019-0010087"
        )

        self.assertFalse(detect_document_bundle([page, "청구항 본문"]).is_multiple)

    def test_two_doi_abstract_title_pages_are_a_bundle(self) -> None:
        first = "First article\ndoi: 10.1000/first\nAbstract\nFirst abstract text."
        second = "Second article\ndoi: 10.1000/second\nAbstract\nSecond abstract text."

        decision = detect_document_bundle([first, "body", second])

        self.assertTrue(decision.is_multiple)
        self.assertEqual(decision.document_count, 2)


if __name__ == "__main__":
    unittest.main()
