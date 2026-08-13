import unittest

from paper_organizer.application.bibliography_lookup import (
    BibliographyLookupService,
)


class FakeGetClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get_json(self, url, headers, timeout_seconds):
        self.urls.append(url)
        if not self.responses:
            return {}
        return self.responses.pop(0)


class BibliographyLookupTests(unittest.TestCase):
    def test_crossref_doi_record_verifies_matching_title(self):
        client = FakeGetClient(
            [
                {
                    "message": {
                        "title": [
                            "Heat shock of Escherichia coli increases binding of DnaK "
                            "(the Hsp70 homolog) to polypeptides by promoting its phosphorylation"
                        ],
                        "author": [
                            {"given": "Michael Y.", "family": "Sherman"},
                            {"given": "Alfred L.", "family": "Goldberg"},
                        ],
                        "container-title": [
                            "Proceedings of the National Academy of Sciences"
                        ],
                        "issued": {"date-parts": [[1993, 1, 1]]},
                        "DOI": "10.1073/pnas.90.18.8648",
                    }
                },
                {"esearchresult": {"idlist": []}},
                {"message": {"items": []}},
            ]
        )
        service = BibliographyLookupService(client)

        result = service.verify(
            title=(
                "Heat shock of Escherichia coli increases binding of dnaK "
                "(the hsp7O homolog) to polypeptides by promoting its phosphorylation"
            ),
            doi="10.1073/pnas.90.18.8648",
        )

        self.assertIsNotNone(result)
        self.assertEqual(
            result.authors,
            ("Michael Y. Sherman", "Alfred L. Goldberg"),
        )
        self.assertEqual(result.year, 1993)
        self.assertEqual(result.source, "verified:crossref")

    def test_pubmed_title_search_supplies_abbreviated_authors(self):
        client = FakeGetClient(
            [
                {"esearchresult": {"idlist": ["8406014"]}},
                {
                    "result": {
                        "8406014": {
                            "title": "Heat shock of Escherichia coli increases binding of DnaK to polypeptides",
                            "authors": [
                                {"name": "Sherman MY"},
                                {"name": "Goldberg AL"},
                            ],
                            "pubdate": "1993 Sep 15",
                            "fulljournalname": "Proceedings of the National Academy of Sciences of the United States of America",
                        }
                    }
                },
                {"message": {"items": []}},
            ]
        )
        service = BibliographyLookupService(client)

        result = service.verify(
            title="Heat shock of Escherichia coli increases binding of DnaK to polypeptides"
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.authors, ("Sherman MY", "Goldberg AL"))
        self.assertEqual(result.year, 1993)
        self.assertEqual(result.source, "verified:pubmed")


if __name__ == "__main__":
    unittest.main()
