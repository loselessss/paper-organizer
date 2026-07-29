import unittest

from paper_organizer.core.patent import (
    patent_index_numbers,
    preferred_patent_number,
)


class PatentNumberTests(unittest.TestCase):
    def test_registration_number_is_preferred_over_application(self):
        self.assertEqual(
            preferred_patent_number("10-2052132", "10-2017-0092335"),
            "10-2052132",
        )
        self.assertEqual(
            patent_index_numbers("10-2052132", "10-2017-0092335"),
            "10-2052132 10-2017-0092335",
        )

    def test_application_number_is_preferred_over_publication(self):
        self.assertEqual(
            preferred_patent_number("10-2019-0010087", "10-2017-0092335"),
            "10-2017-0092335",
        )


if __name__ == "__main__":
    unittest.main()
