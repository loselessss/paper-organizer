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
        self.assertEqual(len(client.urls), 1)

    def test_reference_identifier_cannot_override_unrelated_title(self):
        client = FakeGetClient([
            {"message": {"title": ["Unrelated cancer study"], "author": [{"family": "Other"}]}},
            {"esearchresult": {"idlist": []}},
            {"message": {"items": []}},
        ])
        result = BibliographyLookupService(client).verify(
            title="Bacterial heat shock protein DnaK", doi="10.1234/reference")
        self.assertIsNone(result)
        self.assertEqual(len(client.urls), 3)

    def test_incomplete_identifier_result_continues_to_title_search(self):
        title = "Bacterial heat shock protein DnaK"
        client = FakeGetClient([
            {"message": {"title": [title]}},
            {"esearchresult": {"idlist": []}},
            {"message": {"items": [{"title": [title], "author": [{"family": "Author"}]}]}},
        ])
        result = BibliographyLookupService(client).verify(title=title, doi="10.1234/test")
        self.assertIsNotNone(result)
        self.assertEqual(len(client.urls), 3)

    def test_blank_title_does_not_trust_identifier_alone(self):
        client = FakeGetClient([{"message": {"title": ["Unrelated study"]}}])
        self.assertIsNone(BibliographyLookupService(client).verify(title="", doi="10.1234/test"))

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
