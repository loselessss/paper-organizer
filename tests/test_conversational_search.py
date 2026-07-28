import json
import tempfile
import unittest
from pathlib import Path

import fitz

from paper_organizer.application.conversational_search import (
    ConversationalSearchController,
    requires_ai_search,
)
from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.core.paperpack import build_content_payload, create_paperpack
from paper_organizer.core.search_index import rebuild_search_index
from paper_organizer.infra.settings import load_settings, save_settings


class MemorySecretStore:
    def get(self, provider):
        return "test-key"

    def set(self, provider, secret):
        raise AssertionError("not used")

    def delete(self, provider):
        raise AssertionError("not used")


class SequentialHttpClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def post_json(self, url, headers, payload, timeout_seconds):
        self.calls.append(payload)
        return self.payloads.pop(0)


def openai_response(data):
    return {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": json.dumps(data)}],
            }
        ]
    }


def create_library(root: Path):
    input_dir = root / "downloads"
    library = root / "library"
    input_dir.mkdir()
    settings_path = root / "settings.json"
    workflow = LibraryWorkflowController(settings_path)
    workflow.save_paths(input_dir, library, auto_enabled=False)
    settings = load_settings(settings_path)
    settings.summary_provider = "openai"
    settings.openai_model = "gpt-test"
    settings.cloud_processing_consent = True
    save_settings(settings, settings_path)

    pdf = root / "paper.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf)
    document.close()
    file_id = "sha256:test-paper"
    metadata = {
        "schema_version": 2,
        "id": file_id,
        "file": {
            "sha256": "test-paper",
            "relative_path": "papers/Biology/Enzymes/paper.paperpack",
            "page_count": 2,
        },
        "identity": {
            "file_id": file_id,
            "edition_id": file_id,
            "work_id": "doi:10.1000/test",
            "source_variant": "publisher",
        },
        "bibliography": {
            "title": "Thermostable Enzyme Engineering",
            "authors": ["A. Researcher"],
            "year": 2024,
            "venue": "Enzyme Journal",
        },
        "classification": {
            "category": "생물공학",
            "subcategory": "단백질공학",
            "tags": [],
        },
        "description": {"summary": "", "keywords": []},
        "curation": {"revision": 1, "field_sources": {}},
        "workflow": {"analysis_status": "completed"},
    }
    pack = library / "papers" / "Biology" / "Enzymes" / "paper.paperpack"
    create_paperpack(
        pack,
        pdf,
        metadata,
        content=build_content_payload(
            [
                "We engineered a thermostable enzyme using directed evolution.",
                "The enzyme retained catalytic activity at 80 degrees Celsius.",
            ]
        ),
    )
    rebuild_search_index(library)
    return workflow, settings_path, file_id, pack


class ConversationalSearchTests(unittest.TestCase):
    def test_routes_literal_queries_locally_and_questions_to_ai(self):
        self.assertFalse(requires_ai_search("thermostable enzyme"))
        self.assertFalse(requires_ai_search("10.1000/test"))
        self.assertFalse(requires_ai_search("A. Researcher"))
        self.assertTrue(requires_ai_search("열에 강한 효소를 만든 논문은?"))
        self.assertTrue(requires_ai_search("두 연구의 방법 차이를 비교해줘"))
        self.assertTrue(
            requires_ai_search(
                "papers using directed evolution after 2022 for stable enzymes"
            )
        )

    def test_retrieves_pages_and_filters_unknown_ai_citations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow, settings_path, file_id, pack = create_library(root)
            client = SequentialHttpClient(
                [
                    openai_response(
                        {
                            "search_queries": [
                                "thermostable enzyme",
                                "directed evolution",
                            ],
                            "category": "",
                            "year_from": "",
                            "year_to": "",
                        }
                    ),
                    openai_response(
                        {
                            "answer_ko": "고온 안정성 효소 논문이 있습니다.",
                            "papers": [
                                {
                                    "file_id": file_id,
                                    "pages": [1],
                                    "why": "1쪽에 열안정성 효소 설계가 나옵니다.",
                                },
                                {
                                    "file_id": "sha256:invented",
                                    "pages": [9],
                                    "why": "없는 논문",
                                },
                            ],
                            "confidence": "high",
                        }
                    ),
                ]
            )
            controller = ConversationalSearchController(
                workflow,
                MemorySecretStore(),
                settings_path,
                http_client=client,
            )

            prepared = controller.prepare("열에 강한 효소를 만든 논문은?")
            result = controller.answer(prepared)

            self.assertEqual(len(prepared.candidates), 1)
            self.assertEqual(prepared.candidates[0].sidecar_path, pack.resolve())
            self.assertEqual(prepared.candidates[0].pages, (1,))
            self.assertIn(file_id, prepared.context_text)
            self.assertIn("[PDF PAGE 1]", prepared.context_text)
            self.assertEqual(len(result.answer.papers), 1)
            self.assertEqual(result.answer.papers[0].file_id, file_id)
            self.assertEqual(len(client.calls), 2)

    def test_zero_candidates_skips_answer_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workflow, settings_path, _file_id, _pack = create_library(root)
            client = SequentialHttpClient(
                [
                    openai_response(
                        {
                            "search_queries": ["quantum unicorn lattice"],
                            "category": "",
                            "year_from": "",
                            "year_to": "",
                        }
                    )
                ]
            )
            controller = ConversationalSearchController(
                workflow,
                MemorySecretStore(),
                settings_path,
                http_client=client,
            )

            prepared = controller.prepare("없는 주제")

            self.assertEqual(prepared.candidates, ())
            self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
