import os
import tempfile
import time
import unittest
from pathlib import Path

import fitz

from paper_organizer.application.library_workflow import (
    EditablePaperMetadata,
    LibraryWorkflowController,
    _apply_patent_metadata,
)
from paper_organizer.core.paperpack import load_paperpack_metadata
from paper_organizer.infra.settings import load_settings, save_settings


def protein_pages() -> list[str]:
    return [
        "Directed evolution of a thermostable enzyme scaffold\n"
        "Journal of Molecular Biology 431 (2019) 1121-1134\n"
        "Abstract We report protein engineering of an enzyme using directed "
        "evolution and rational design. doi:10.1016/j.jmb.2019.01.001\n"
        "Introduction Protein engineering has become central to biotechnology "
        "because enzymes can be tailored for industrial biocatalysis. "
        "Mutagenesis libraries were screened for protein stability across many "
        "rounds of selection and characterization.",
        "Methods Recombinant protein expression in Escherichia coli was followed "
        "by chromatography purification. Catalytic efficiency and kcat were "
        "measured for each enzyme variant obtained by directed evolution.\n"
        "Results The engineered enzyme retained activity at elevated temperature.\n"
        "References 1. Smith et al. protein engineering review.",
    ]


def write_pdf(path: Path, pages: list[str]) -> None:
    pages = [*pages, *(["Test fixture continuation"] * max(0, 3 - len(pages)))]
    document = fitz.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(fitz.Rect(35, 35, 560, 800), text, fontsize=8)
    document.save(path)
    document.close()
    old = time.time() - 120
    os.utime(path, (old, old))


def patent_pages() -> list[str]:
    return [
        "US Patent Application\nPublication Number US20260000001\n"
        "Inventor: Example Inventor\nApplicant: Example Labs\n"
        + "A system and method for organizing technical documents. " * 20,
        "Detailed Description\n" + "The disclosed system processes documents. " * 25,
        "Claims\n1. A method comprising receiving and classifying a document. " * 20,
    ]


def us_inid_patent_pages() -> list[str]:
    return [
        "(12) United States Patent\n"
        "(10) Patent No.: US 10,000,001 B2\n"
        "(45) Date of Patent: June 1, 2021\n"
        "(54) CRISPR-CAS9 GENOME EDITING METHODS\n"
        "(71) Applicant: Genome Editing Institute\n"
        "(72) Inventors: Jennifer A. Doudna; Emmanuelle Charpentier\n"
        "(21) Appl. No.: 15/123,456\n"
        + "The disclosure describes CRISPR Cas9 genome editing systems. " * 15,
        "Detailed Description\n"
        + "Cas9 and guide RNA form a genome editing complex. " * 20,
        "Claims\n1. A method of editing a target DNA sequence using Cas9. " * 20,
    ]


def korean_inid_patent_pages() -> list[str]:
    return [
        "(19) 대한민국특허청(KR)\n"
        "(12) 등록특허공보(B1)\n"
        "(11) 등록번호 10-2345678\n"
        "(45) 공고일자 2024년 03월 12일\n"
        "(54) 발명의 명칭\n"
        "CRISPR-CAS9을 이용한 유전자 편집 방법\n"
        "(71) 출원인\n"
        "유전체편집연구소\n"
        "서울특별시 성북구 연구로 123\n"
        "(72) 발명자\n"
        "김유전자\n"
        "서울특별시 성북구 연구로 123, 101동 202호\n"
        "이단백질\n"
        "경기도 수원시 실험로 45\n"
        "(21) 출원번호 10-2020-0012345\n"
        "(22) 출원일자 2020년 02월 03일\n"
        + "본 발명은 CRISPR Cas9 유전자 편집 시스템에 관한 것이다. " * 15,
        "발명의 상세한 설명\n"
        + "Cas9과 가이드 RNA를 이용하여 표적 서열을 편집한다. " * 20,
        "청구범위\n1. Cas9을 이용하여 표적 DNA를 편집하는 방법. " * 20,
        "(19) 대한민국특허청(KR)\n"
        "(12) 등록특허공보(B1)\n"
        "(11) 등록번호 10-9999999\n"
        "(54) 발명의 명칭 뒤쪽에 묶인 다른 특허\n"
        "(72) 발명자 다른사람\n"
        "(73) 특허권자 다른기관\n",
    ]


class AutoOrganizeTests(unittest.TestCase):
    def test_patent_is_included_in_automatic_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "patent.pdf", patent_pages())

            controller.scan()
            result = controller.scan()

            self.assertEqual(len(result.auto_organized), 1)
            pack = next((library / "papers").rglob("*.paperpack"))
            record = load_paperpack_metadata(pack)
            self.assertTrue(record["detection"]["is_patent"])
            self.assertEqual(record["detection"]["document_type"], "patent")
            self.assertEqual(record["document"]["type"], "patent")
            self.assertEqual(record["bibliography"]["venue"], "")
            self.assertEqual(record["bibliography"]["authors"], ["Example Inventor"])
            self.assertEqual(record["patent"]["office"], "USPTO")
            self.assertEqual(record["patent"]["publication_number"], "US20260000001")
            self.assertEqual(record["patent"]["assignee"], "Example Labs")

    def test_us_inid_patent_bibliography_is_extracted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "cas9-patent.pdf", us_inid_patent_pages())

            controller.scan()
            result = controller.scan()

            self.assertEqual(len(result.auto_organized), 1)
            pack = next((library / "papers").rglob("*.paperpack"))
            record = load_paperpack_metadata(pack)
            self.assertEqual(
                record["bibliography"]["title"],
                "CRISPR-CAS9 GENOME EDITING METHODS",
            )
            self.assertEqual(
                record["bibliography"]["authors"],
                ["Jennifer A. Doudna", "Emmanuelle Charpentier"],
            )
            self.assertEqual(record["bibliography"]["year"], 2021)
            self.assertEqual(record["bibliography"]["venue"], "")
            self.assertEqual(record["patent"]["office"], "USPTO")
            self.assertEqual(
                record["patent"]["publication_number"],
                "US 10,000,001 B2",
            )
            self.assertEqual(record["patent"]["application_number"], "15/123,456")
            self.assertEqual(
                record["patent"]["assignee"],
                "Genome Editing Institute",
            )

    def test_korean_inid_patent_bibliography_is_extracted(self):
        metadata = _apply_patent_metadata(
            EditablePaperMetadata(title="cas9-korean-patent"),
            korean_inid_patent_pages(),
        )

        self.assertEqual(
            metadata.title,
            "CRISPR-CAS9을 이용한 유전자 편집 방법",
        )
        self.assertEqual(metadata.authors, ["김유전자", "이단백질"])
        self.assertEqual(metadata.year, 2024)
        self.assertEqual(metadata.venue, "")
        self.assertEqual(metadata.patent_office, "KIPO")
        self.assertEqual(metadata.publication_number, "10-2345678")
        self.assertEqual(metadata.application_number, "10-2020-0012345")
        self.assertEqual(metadata.assignee, "유전체편집연구소")

    def _controller(self, root: Path, *, auto_organize: bool = True):
        input_dir = root / "downloads"
        library = root / "library"
        input_dir.mkdir()
        settings_path = root / "settings.json"
        controller = LibraryWorkflowController(settings_path)
        controller.save_paths(
            input_dir,
            library,
            auto_enabled=False,
            auto_organize_academic=auto_organize,
        )
        settings = load_settings(settings_path)
        settings.minimum_age_seconds = 0
        save_settings(settings, settings_path)
        return controller, input_dir, library

    def test_academic_paper_is_stored_and_queued_without_approval(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "paper.pdf", protein_pages())

            controller.scan()
            result = controller.scan()

            self.assertEqual(result.items, ())
            self.assertEqual(len(result.auto_organized), 1)
            packs = list((library / "papers").rglob("*.paperpack"))
            self.assertEqual(len(packs), 1)
            self.assertEqual(packs[0].parent.parent.name, "생물공학")
            self.assertEqual(packs[0].parent.name, "단백질공학")

            queue = controller.analysis_queue()
            self.assertEqual(len(queue), 1)
            self.assertEqual(queue[0].status, "organized_pending_analysis")

            record = load_paperpack_metadata(packs[0])
            sources = record["curation"]["field_sources"]
            self.assertEqual(sources["classification.category"], "auto:regex")
            self.assertEqual(record["bibliography"]["venue"], "Journal of Molecular Biology")

    def test_auto_organize_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root, auto_organize=False)
            write_pdf(input_dir / "paper.pdf", protein_pages())

            controller.scan()
            result = controller.scan()

            self.assertEqual(len(result.items), 1)
            self.assertEqual(result.auto_organized, ())
            self.assertEqual(list((library / "papers").rglob("*.paperpack")), [])
            self.assertEqual(controller.analysis_queue()[0].status, "pending_review")

    def test_duplicate_candidates_stay_for_human_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "first.pdf", protein_pages())
            controller.scan()
            controller.scan()
            self.assertEqual(len(list((library / "papers").rglob("*.paperpack"))), 1)

            write_pdf(input_dir / "second.pdf", protein_pages())
            controller.scan()
            result = controller.scan()

            self.assertEqual(len(result.items), 1)
            self.assertEqual(result.auto_organized, ())
            self.assertIsNotNone(result.items[0].duplicate)
            self.assertEqual(len(list((library / "papers").rglob("*.paperpack"))), 1)

    def test_non_academic_pdf_stays_for_human_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            write_pdf(input_dir / "notes.pdf", ["짧은 메모입니다."])

            controller.scan()
            result = controller.scan()

            self.assertEqual(len(result.items), 1)
            self.assertNotEqual(result.items[0].detection_status, "academic_likely")
            self.assertEqual(result.auto_organized, ())
            self.assertEqual(list((library / "papers").rglob("*.paperpack")), [])

    def test_focus_categories_limit_auto_classification(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, library = self._controller(root)
            settings_path = root / "settings.json"
            settings = load_settings(settings_path)
            settings.focus_categories = ["의학"]
            save_settings(settings, settings_path)
            write_pdf(input_dir / "paper.pdf", protein_pages())

            controller.scan()
            controller.scan()

            packs = list((library / "papers").rglob("*.paperpack"))
            self.assertEqual(len(packs), 1)
            self.assertEqual(packs[0].parent.parent.name, "Uncategorized")

    def test_manual_organize_still_records_user_as_field_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root, auto_organize=False)
            write_pdf(input_dir / "paper.pdf", protein_pages())
            controller.scan()
            item = controller.scan().items[0]

            organized = controller.organize(
                item, EditablePaperMetadata(title="Curated", category="생물공학")
            )

            record = load_paperpack_metadata(organized.pdf_path)
            self.assertEqual(
                record["curation"]["field_sources"]["classification.category"], "user"
            )

    def test_suggest_metadata_fills_category_and_venue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller, input_dir, _library = self._controller(root, auto_organize=False)
            write_pdf(input_dir / "paper.pdf", protein_pages())
            controller.scan()
            item = controller.scan().items[0]

            suggested = controller.suggest_metadata(item)

            self.assertEqual(suggested.category, "생물공학")
            self.assertEqual(suggested.subcategory, "단백질공학")
            self.assertEqual(suggested.venue, "Journal of Molecular Biology")


if __name__ == "__main__":
    unittest.main()
