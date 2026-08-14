"""Diagnostics for search-text normalization side effects."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from paper_organizer.core.paperpack import (
    PaperPackError,
    content_pages,
    iter_paperpacks,
    load_paperpack_content,
    load_paperpack_metadata,
)
from paper_organizer.core.search_index import (
    SearchIndexError,
    _fts_query,
    fts5_available,
    normalize_search_text,
)


_TOKEN_RE = re.compile(r"[A-Za-z가-힣][A-Za-z가-힣0-9_-]{2,}")
_STOPWORDS = {
    "abstract",
    "article",
    "background",
    "conclusion",
    "discussion",
    "figure",
    "introduction",
    "journal",
    "method",
    "methods",
    "paper",
    "references",
    "result",
    "results",
    "study",
    "table",
}
_PAGE_MARKER_LINE_RE = re.compile(
    r"(?i)^\s*(?:\[?\s*(?:PDF\s+)?PAGE\s+\d+\s*\]?|\d+|\d+\s*/\s*\d+|-\s*\d+\s*-)\s*$"
)
_SOFT_HYPHEN_LINEBREAK_RE = re.compile(
    r"(?<=[A-Za-z])-\s*\r?\n\s*(?=[A-Za-z])"
)


@dataclass(frozen=True, slots=True)
class SearchNormalizationDrop:
    query: str
    raw_hits: int
    normalized_hits: int
    examples: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchNormalizationAudit:
    paperpacks: int
    pages: int
    changed_pages: int
    removed_marker_lines: int
    soft_hyphen_joins: int
    queries: tuple[str, ...]
    drops: tuple[SearchNormalizationDrop, ...]
    gains: tuple[SearchNormalizationDrop, ...]
    removed_token_sample: tuple[str, ...]
    problems: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _AuditPage:
    document_id: str
    title: str
    page: int
    raw_text: str
    normalized_text: str


def audit_search_normalization(
    library_root: Path,
    *,
    queries: tuple[str, ...] = (),
    sample_limit: int = 80,
) -> SearchNormalizationAudit:
    """Compare raw page text and normalized FTS text without writing caches."""

    if not fts5_available():
        raise SearchIndexError(
            "이 파이썬의 SQLite가 FTS5를 지원하지 않아 검색 정규화 진단을 할 수 없습니다."
        )
    root = library_root.expanduser().resolve()
    pages, paperpack_count, problems = _load_audit_pages(root)
    audit_queries = tuple(dict.fromkeys(query.strip() for query in queries if query.strip()))
    if not audit_queries:
        audit_queries = _sample_queries(pages, sample_limit=sample_limit)
    raw_hits, normalized_hits = _compare_queries(pages, audit_queries)
    drops: list[SearchNormalizationDrop] = []
    gains: list[SearchNormalizationDrop] = []
    titles_by_doc = {page.document_id: page.title for page in pages}
    for query in audit_queries:
        raw_docs = raw_hits.get(query, set())
        normalized_docs = normalized_hits.get(query, set())
        if len(normalized_docs) < len(raw_docs):
            missing = tuple(
                titles_by_doc[document_id]
                for document_id in sorted(raw_docs - normalized_docs)
            )
            drops.append(
                SearchNormalizationDrop(
                    query,
                    len(raw_docs),
                    len(normalized_docs),
                    missing[:5],
                )
            )
        elif len(normalized_docs) > len(raw_docs):
            gained = tuple(
                titles_by_doc[document_id]
                for document_id in sorted(normalized_docs - raw_docs)
            )
            gains.append(
                SearchNormalizationDrop(
                    query,
                    len(raw_docs),
                    len(normalized_docs),
                    gained[:5],
                )
            )
    return SearchNormalizationAudit(
        paperpacks=paperpack_count,
        pages=len(pages),
        changed_pages=sum(page.raw_text != page.normalized_text for page in pages),
        removed_marker_lines=sum(_removed_marker_lines(page.raw_text) for page in pages),
        soft_hyphen_joins=sum(
            len(_SOFT_HYPHEN_LINEBREAK_RE.findall(page.raw_text)) for page in pages
        ),
        queries=audit_queries,
        drops=tuple(sorted(drops, key=lambda item: item.raw_hits - item.normalized_hits, reverse=True)),
        gains=tuple(sorted(gains, key=lambda item: item.normalized_hits - item.raw_hits, reverse=True)),
        removed_token_sample=_removed_token_sample(pages),
        problems=tuple(problems),
    )


def _load_audit_pages(root: Path) -> tuple[list[_AuditPage], int, list[str]]:
    pages: list[_AuditPage] = []
    problems: list[str] = []
    paperpack_count = 0
    paperpacks = tuple(sorted(iter_paperpacks(root)))
    if not paperpacks:
        paperpacks = tuple(sorted(root.rglob("*.paperpack"))) if root.is_dir() else ()
    for paperpack in paperpacks:
        paperpack_count += 1
        try:
            record = load_paperpack_metadata(paperpack)
            title = str(
                record.get("bibliography", {}).get("title")
                or paperpack.stem
            )
            document_id = str(
                record.get("identity", {}).get("file_id")
                or record.get("id")
                or paperpack.resolve()
            )
            for page, text in content_pages(load_paperpack_content(paperpack)):
                pages.append(
                    _AuditPage(
                        document_id=document_id,
                        title=title,
                        page=page,
                        raw_text=text,
                        normalized_text=normalize_search_text(text),
                    )
                )
        except (OSError, ValueError, TypeError, PaperPackError) as exc:
            problems.append(f"{paperpack.name}: {exc}")
    return pages, paperpack_count, problems


def _sample_queries(pages: list[_AuditPage], *, sample_limit: int) -> tuple[str, ...]:
    document_frequency: Counter[str] = Counter()
    for page in pages:
        tokens = {
            token.casefold()
            for token in _TOKEN_RE.findall(page.normalized_text)
            if token.casefold() not in _STOPWORDS
        }
        document_frequency.update(tokens)
    ranked = sorted(
        document_frequency.items(),
        key=lambda item: (-item[1], item[0]),
    )
    return tuple(token for token, _count in ranked[: max(1, sample_limit)])


def _compare_queries(
    pages: list[_AuditPage],
    queries: tuple[str, ...],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(
            """
            CREATE VIRTUAL TABLE raw_pages USING fts5(
                document_id UNINDEXED,
                text,
                tokenize = "unicode61 remove_diacritics 2"
            );
            CREATE VIRTUAL TABLE normalized_pages USING fts5(
                document_id UNINDEXED,
                text,
                tokenize = "unicode61 remove_diacritics 2"
            );
            """
        )
        connection.executemany(
            "INSERT INTO raw_pages(document_id, text) VALUES (?, ?)",
            [(page.document_id, page.raw_text) for page in pages if page.raw_text.strip()],
        )
        connection.executemany(
            "INSERT INTO normalized_pages(document_id, text) VALUES (?, ?)",
            [
                (page.document_id, page.normalized_text)
                for page in pages
                if page.normalized_text.strip()
            ],
        )
        raw = {
            query: _query_docs(connection, "raw_pages", query)
            for query in queries
        }
        normalized = {
            query: _query_docs(connection, "normalized_pages", query)
            for query in queries
        }
    return raw, normalized


def _query_docs(
    connection: sqlite3.Connection,
    table: str,
    query: str,
) -> set[str]:
    try:
        statement = _fts_query(query)
    except SearchIndexError:
        return set()
    return {
        str(row[0])
        for row in connection.execute(
            f"SELECT DISTINCT document_id FROM {table} WHERE {table} MATCH ?",
            (statement,),
        ).fetchall()
    }


def _removed_marker_lines(text: str) -> int:
    return sum(
        1
        for line in str(text or "").splitlines()
        if _PAGE_MARKER_LINE_RE.fullmatch(line)
    )


def _removed_token_sample(pages: list[_AuditPage]) -> tuple[str, ...]:
    removed: Counter[str] = Counter()
    for page in pages:
        raw_tokens = {token.casefold() for token in _TOKEN_RE.findall(page.raw_text)}
        normalized_tokens = {
            token.casefold() for token in _TOKEN_RE.findall(page.normalized_text)
        }
        removed.update(
            token
            for token in raw_tokens - normalized_tokens
            if token not in _STOPWORDS
        )
    return tuple(
        token
        for token, _count in sorted(
            removed.items(),
            key=lambda item: (-item[1], item[0]),
        )[:20]
    )
