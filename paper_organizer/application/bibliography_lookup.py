"""External bibliography verification through public scholarly metadata APIs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, quote_plus
from urllib.request import Request, urlopen


_PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d{5,10})\b", re.IGNORECASE)
_PMCID_RE = re.compile(r"\bPMC(?:ID)?\s*:?\s*(PMC\d{5,10})\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


@dataclass(frozen=True, slots=True)
class VerifiedBibliography:
    title: str = ""
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str = ""
    doi: str = ""
    pmid: str = ""
    pmcid: str = ""
    source: str = ""
    score: float = 0.0
    matched_identifier: str = ""


class JsonGetClient(Protocol):
    def get_json(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        ...


class UrllibJsonGetClient:
    def get_json(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise BibliographyLookupError(f"서지 조회 HTTP 오류: {exc.code}") from None
        except URLError:
            raise BibliographyLookupError("서지 조회 서버에 연결할 수 없습니다.") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BibliographyLookupError("서지 조회 응답이 올바른 JSON이 아닙니다.") from None
        if not isinstance(raw, dict):
            raise BibliographyLookupError("서지 조회 응답은 JSON 객체여야 합니다.")
        return raw


class BibliographyLookupError(RuntimeError):
    pass


@dataclass(slots=True)
class BibliographyLookupService:
    http_client: JsonGetClient = field(default_factory=UrllibJsonGetClient)
    timeout_seconds: float = 4.0

    def verify(
        self,
        *,
        title: str,
        doi: str = "",
        page_texts: Sequence[str] = (),
    ) -> VerifiedBibliography | None:
        """Return a trusted bibliography record only when it matches the PDF title."""

        front_text = "\n".join(page_texts[:4])
        pmid = _extract_pmid(front_text)
        pmcid = _extract_pmcid(front_text)
        candidates: list[VerifiedBibliography] = []
        for candidate in (
            self._pubmed_by_pmid(pmid) if pmid else None,
            self._pubmed_by_pmcid(pmcid) if pmcid else None,
            self._crossref_by_doi(doi) if doi else None,
            self._pubmed_by_title(title),
            self._crossref_by_title(title),
        ):
            if candidate is not None:
                candidates.append(candidate)
        scored = [
            _with_score(candidate, title)
            for candidate in candidates
            if candidate.title.strip()
        ]
        scored = [
            candidate
            for candidate in scored
            if candidate.matched_identifier or not title.strip() or candidate.score >= 0.72
        ]
        if not scored:
            return None
        return max(
            scored,
            key=lambda item: (
                bool(item.matched_identifier),
                bool(item.authors),
                item.score,
            ),
        )

    def _pubmed_by_pmid(self, pmid: str) -> VerifiedBibliography | None:
        return self._pubmed_summary(pmid, matched_identifier=f"pmid:{pmid}")

    def _pubmed_by_pmcid(self, pmcid: str) -> VerifiedBibliography | None:
        query = quote_plus(f"{pmcid.strip()}[AID]")
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&retmode=json&retmax=1&term={query}"
        )
        try:
            payload = self.http_client.get_json(url, _headers(), self.timeout_seconds)
        except BibliographyLookupError:
            return None
        result = payload.get("esearchresult")
        ids = result.get("idlist") if isinstance(result, dict) else []
        if not isinstance(ids, list) or not ids:
            return None
        return self._pubmed_summary(
            str(ids[0]),
            matched_identifier=f"pmcid:{pmcid.strip()}",
        )

    def _pubmed_by_title(self, title: str) -> VerifiedBibliography | None:
        if not title.strip():
            return None
        query = quote_plus(f'"{title.strip()}"[Title]')
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
            f"?db=pubmed&retmode=json&retmax=1&term={query}"
        )
        try:
            payload = self.http_client.get_json(url, _headers(), self.timeout_seconds)
        except BibliographyLookupError:
            return None
        result = payload.get("esearchresult")
        ids = result.get("idlist") if isinstance(result, dict) else []
        if not isinstance(ids, list) or not ids:
            return None
        return self._pubmed_summary(str(ids[0]))

    def _pubmed_summary(
        self,
        pmid: str,
        *,
        matched_identifier: str = "",
    ) -> VerifiedBibliography | None:
        url = (
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
            f"?db=pubmed&retmode=json&id={quote(pmid)}"
        )
        try:
            payload = self.http_client.get_json(url, _headers(), self.timeout_seconds)
        except BibliographyLookupError:
            return None
        result = payload.get("result")
        if not isinstance(result, dict):
            return None
        item = result.get(pmid)
        if not isinstance(item, dict):
            return None
        authors = []
        for author in item.get("authors") or []:
            if isinstance(author, dict):
                name = str(author.get("name") or "").strip()
                if name:
                    authors.append(name)
        doi = ""
        pmcid = ""
        for article_id in item.get("articleids") or []:
            if not isinstance(article_id, dict):
                continue
            kind = str(article_id.get("idtype") or "").casefold()
            value = str(article_id.get("value") or "").strip()
            if kind == "doi":
                doi = value.casefold()
            elif kind == "pmc":
                pmcid = value
        return VerifiedBibliography(
            title=str(item.get("title") or "").strip().rstrip("."),
            authors=tuple(authors),
            year=_year_from_text(str(item.get("pubdate") or "")),
            venue=str(item.get("fulljournalname") or item.get("source") or "").strip(),
            doi=doi,
            pmid=pmid,
            pmcid=pmcid,
            source="verified:pubmed",
            matched_identifier=matched_identifier,
        )

    def _crossref_by_doi(self, doi: str) -> VerifiedBibliography | None:
        normalized = doi.strip().casefold()
        if not normalized:
            return None
        url = f"https://api.crossref.org/v1/works/{quote(normalized, safe='')}"
        payload = self._crossref_get(url)
        message = payload.get("message") if payload else None
        if not isinstance(message, dict):
            return None
        return _crossref_record(message, matched_identifier=f"doi:{normalized}")

    def _crossref_by_title(self, title: str) -> VerifiedBibliography | None:
        if not title.strip():
            return None
        url = (
            "https://api.crossref.org/v1/works"
            f"?rows=3&query.title={quote_plus(title.strip())}"
        )
        payload = self._crossref_get(url)
        message = payload.get("message") if payload else None
        items = message.get("items") if isinstance(message, dict) else None
        if not isinstance(items, list):
            return None
        records = [
            _crossref_record(item)
            for item in items
            if isinstance(item, dict)
        ]
        records = [record for record in records if record is not None]
        if not records:
            return None
        return max(records, key=lambda item: _title_score(title, item.title))

    def _crossref_get(self, url: str) -> Mapping[str, Any]:
        try:
            return self.http_client.get_json(url, _headers(), self.timeout_seconds)
        except BibliographyLookupError:
            return {}


def _crossref_record(
    message: Mapping[str, Any],
    *,
    matched_identifier: str = "",
) -> VerifiedBibliography | None:
    titles = message.get("title")
    title = str(titles[0]).strip() if isinstance(titles, list) and titles else ""
    if not title:
        return None
    authors = []
    for author in message.get("author") or []:
        if not isinstance(author, dict):
            continue
        name = str(author.get("name") or "").strip()
        if not name:
            parts = [
                str(author.get(key) or "").strip()
                for key in ("given", "family")
                if str(author.get(key) or "").strip()
            ]
            name = " ".join(parts)
        if name:
            authors.append(name)
    venues = message.get("container-title")
    venue = str(venues[0]).strip() if isinstance(venues, list) and venues else ""
    return VerifiedBibliography(
        title=title,
        authors=tuple(authors),
        year=_crossref_year(message),
        venue=venue,
        doi=str(message.get("DOI") or "").strip().casefold(),
        source="verified:crossref",
        matched_identifier=matched_identifier,
    )


def _crossref_year(message: Mapping[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "published", "issued"):
        value = message.get(key)
        if not isinstance(value, dict):
            continue
        parts = value.get("date-parts")
        if (
            isinstance(parts, list)
            and parts
            and isinstance(parts[0], list)
            and parts[0]
        ):
            try:
                year = int(parts[0][0])
            except (TypeError, ValueError):
                continue
            if 1000 <= year <= 9999:
                return year
    return None


def _headers() -> Mapping[str, str]:
    return {"User-Agent": "paper-organizer/metadata-verification"}


def _extract_pmid(text: str) -> str:
    match = _PMID_RE.search(text)
    return match.group(1) if match else ""


def _extract_pmcid(text: str) -> str:
    match = _PMCID_RE.search(text)
    return match.group(1) if match else ""


def _year_from_text(text: str) -> int | None:
    match = _YEAR_RE.search(text)
    return int(match.group(0)) if match else None


def _title_score(left: str, right: str) -> float:
    left_norm = _normalize_title(left)
    right_norm = _normalize_title(right)
    if not left_norm or not right_norm:
        return 0.0
    ratio = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = set(left_norm.split())
    right_tokens = set(right_norm.split())
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    return round((ratio * 0.65) + (overlap * 0.35), 3)


def _normalize_title(value: str) -> str:
    text = value.casefold().replace("0", "o")
    text = re.sub(r"[^a-z0-9가-힣]+", " ", text)
    return " ".join(text.split())


def _with_score(
    bibliography: VerifiedBibliography,
    title: str,
) -> VerifiedBibliography:
    return VerifiedBibliography(
        title=bibliography.title,
        authors=bibliography.authors,
        year=bibliography.year,
        venue=bibliography.venue,
        doi=bibliography.doi,
        pmid=bibliography.pmid,
        pmcid=bibliography.pmcid,
        source=bibliography.source,
        score=_title_score(title, bibliography.title),
        matched_identifier=bibliography.matched_identifier,
    )
