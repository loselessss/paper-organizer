"""Normalize the representative identifier used for patent display and search."""

from __future__ import annotations

import re


_KOREAN_REGISTRATION_RE = re.compile(
    r"^(?:KR\s*)?10-\d{6,8}(?:\s*[A-Z]\d?)?$",
    re.IGNORECASE,
)
_GRANT_KIND_RE = re.compile(r"\b(?:B|C|U|Y)\d?\s*$", re.IGNORECASE)


def looks_like_registration_number(value: str) -> bool:
    """Return whether a legacy publication field visibly contains a grant number."""

    number = " ".join(str(value or "").split()).strip()
    if not number:
        return False
    return bool(
        _KOREAN_REGISTRATION_RE.fullmatch(number)
        or _GRANT_KIND_RE.search(number)
    )


def preferred_patent_number(
    publication_or_registration: str,
    application_number: str,
) -> str:
    """Prefer a registration number, otherwise use the application identifier."""

    published = " ".join(str(publication_or_registration or "").split()).strip()
    application = " ".join(str(application_number or "").split()).strip()
    if looks_like_registration_number(published):
        return published
    return application or published


def patent_index_numbers(
    publication_or_registration: str,
    application_number: str,
) -> str:
    """Return both identifiers for search with the representative one first."""

    values = [
        preferred_patent_number(
            publication_or_registration,
            application_number,
        ),
        " ".join(str(publication_or_registration or "").split()).strip(),
        " ".join(str(application_number or "").split()).strip(),
    ]
    return " ".join(dict.fromkeys(value for value in values if value))
