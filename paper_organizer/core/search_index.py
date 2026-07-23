# paperpack 본문·메타데이터로 재생성되는 SQLite FTS5 전문 검색 캐시
"""Disposable SQLite FTS cache built from paperpacks.

`.paperpack`이 진실의 원천이고 이 DB는 언제든 삭제 후 재생성할 수 있는
파생 캐시다. FTS5를 쓸 수 없는 환경에서는 호출자가 기존 메모리 검색으로
되돌아갈 수 있도록 SearchIndexUnavailable을 던진다.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paper_organizer.core.paperpack import (
    PAPERPACK_SUFFIX,
    PaperPackError,
    content_pages,
    iter_paperpacks,
    load_paperpack_content,
    load_paperpack_metadata,
)

SEARCH_INDEX_SCHEMA_VERSION = 1
_SNIPPET_TOKENS = 12


class SearchIndexError(RuntimeError):
    pass


class SearchIndexUnavailable(SearchIndexError):
    """The local SQLite build has no FTS5 support."""


@dataclass(frozen=True, slots=True)
class SearchHit:
    file_id: str
    relative_path: str
    title: str
    venue: str
    year: str
    category: str
    subcategory: str
    page: int
    snippet: str


def search_index_path(library_root: Path) -> Path:
    return library_root / "index" / "search.sqlite"


def fts5_available() -> bool:
    try:
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.Error:
        return False


@contextmanager
def _connect(path: Path):
    """Open the cache with the schema ready and always close the handle.

    sqlite3의 컨텍스트 매니저는 트랜잭션만 관리하고 연결은 닫지 않는다.
    Windows에서 파일이 잠긴 채 남지 않도록 여기에서 직접 닫는다.
    """

    if not fts5_available():
        raise SearchIndexUnavailable(
            "이 파이썬의 SQLite가 FTS5를 지원하지 않아 전문 검색을 쓸 수 없습니다."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    with closing(connection):
        _create_schema(connection)
        yield connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS works (
            file_id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            authors TEXT NOT NULL DEFAULT '',
            year TEXT NOT NULL DEFAULT '',
            venue TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            subcategory TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '',
            summary_ko TEXT NOT NULL DEFAULT '',
            indexed_at TEXT NOT NULL DEFAULT ''
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
            file_id UNINDEXED,
            page UNINDEXED,
            text,
            tokenize = "unicode61 remove_diacritics 2"
        );
        """
    )
    connection.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
        (str(SEARCH_INDEX_SCHEMA_VERSION),),
    )


def _text_list(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _work_row(record: dict[str, Any], relative_path: str, indexed_at: str) -> tuple:
    identity = record.get("identity", {})
    bibliography = record.get("bibliography", {})
    classification = record.get("classification", {})
    description = record.get("description", {})
    file_id = str(identity.get("file_id") or record.get("id") or "")
    return (
        file_id,
        relative_path,
        str(bibliography.get("title") or ""),
        _text_list(bibliography.get("authors")),
        str(bibliography.get("year") or ""),
        str(bibliography.get("venue") or ""),
        str(classification.get("category") or ""),
        str(classification.get("subcategory") or ""),
        _text_list(classification.get("tags")),
        str(description.get("summary_ko") or ""),
        indexed_at,
    )


def _upsert_paperpack(
    connection: sqlite3.Connection,
    library_root: Path,
    paperpack: Path,
    indexed_at: str,
) -> str:
    record = load_paperpack_metadata(paperpack)
    relative_path = paperpack.resolve().relative_to(library_root.resolve()).as_posix()
    row = _work_row(record, relative_path, indexed_at)
    file_id = row[0]
    if not file_id:
        raise SearchIndexError(f"file_id가 없는 paperpack입니다: {paperpack.name}")
    connection.execute("DELETE FROM works WHERE file_id = ?", (file_id,))
    connection.execute("DELETE FROM pages WHERE file_id = ?", (file_id,))
    connection.execute(
        "INSERT INTO works VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row
    )
    pages = content_pages(load_paperpack_content(paperpack))
    if pages:
        connection.executemany(
            "INSERT INTO pages(file_id, page, text) VALUES (?, ?, ?)",
            [(file_id, number, text) for number, text in pages],
        )
    return file_id


def rebuild_search_index(
    library_root: Path, *, progress=None
) -> tuple[int, tuple[str, ...]]:
    """Rebuild the whole cache from every paperpack in the library."""

    root = library_root.expanduser().resolve()
    target = search_index_path(root)
    problems: list[str] = []
    indexed = 0
    packs = sorted(iter_paperpacks(root))
    indexed_at = datetime.now(timezone.utc).isoformat()
    with _connect(target) as connection:
        connection.execute("DELETE FROM works")
        connection.execute("DELETE FROM pages")
        for index, paperpack in enumerate(packs, start=1):
            if progress is not None:
                progress(index, len(packs), paperpack.name)
            try:
                _upsert_paperpack(connection, root, paperpack, indexed_at)
                indexed += 1
            except (OSError, ValueError, PaperPackError, SearchIndexError) as exc:
                problems.append(f"{paperpack.name}: {exc}")
        connection.commit()
    return indexed, tuple(problems)


def update_search_entry(library_root: Path, paperpack: Path) -> None:
    """Refresh a single paperpack after it was created or edited."""

    root = library_root.expanduser().resolve()
    source = paperpack.expanduser().resolve()
    if source.suffix.casefold() != PAPERPACK_SUFFIX:
        raise SearchIndexError("paperpack만 검색 색인에 넣을 수 있습니다.")
    with _connect(search_index_path(root)) as connection:
        _upsert_paperpack(
            connection, root, source, datetime.now(timezone.utc).isoformat()
        )
        connection.commit()


def remove_search_entry(library_root: Path, file_id: str) -> None:
    root = library_root.expanduser().resolve()
    target = search_index_path(root)
    if not target.is_file():
        return
    with _connect(target) as connection:
        connection.execute("DELETE FROM works WHERE file_id = ?", (file_id,))
        connection.execute("DELETE FROM pages WHERE file_id = ?", (file_id,))
        connection.commit()


def _fts_query(query: str) -> str:
    tokens = [
        token.replace('"', " ").strip()
        for token in query.split()
        if token.replace('"', " ").strip()
    ]
    if not tokens:
        raise SearchIndexError("검색어를 입력하세요.")
    return " AND ".join(f'"{token}"*' for token in tokens)


def search(
    library_root: Path, query: str, *, limit: int = 50
) -> list[SearchHit]:
    """Search stored full text and return one best-matching page per paper."""

    root = library_root.expanduser().resolve()
    target = search_index_path(root)
    if not target.is_file():
        return []
    statement = _fts_query(query)
    with _connect(target) as connection:
        rows = connection.execute(
            f"""
            SELECT
                works.file_id AS file_id,
                works.relative_path AS relative_path,
                works.title AS title,
                works.venue AS venue,
                works.year AS year,
                works.category AS category,
                works.subcategory AS subcategory,
                pages.page AS page,
                snippet(pages, 2, '[', ']', '…', {_SNIPPET_TOKENS}) AS snippet,
                bm25(pages) AS score
            FROM pages
            JOIN works ON works.file_id = pages.file_id
            WHERE pages MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (statement, max(1, limit) * 8),
        ).fetchall()
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for row in rows:
        if row["file_id"] in seen:
            continue
        seen.add(row["file_id"])
        hits.append(
            SearchHit(
                file_id=row["file_id"],
                relative_path=row["relative_path"],
                title=row["title"],
                venue=row["venue"],
                year=row["year"],
                category=row["category"],
                subcategory=row["subcategory"],
                page=int(row["page"]),
                snippet=" ".join(str(row["snippet"] or "").split()),
            )
        )
        if len(hits) >= limit:
            break
    return hits


def indexed_file_ids(library_root: Path) -> set[str]:
    target = search_index_path(library_root.expanduser().resolve())
    if not target.is_file():
        return set()
    with _connect(target) as connection:
        return {
            str(row["file_id"])
            for row in connection.execute("SELECT file_id FROM works").fetchall()
        }


def search_metadata(
    library_root: Path, query: str, *, limit: int = 50
) -> list[SearchHit]:
    """Fallback that searches only stored metadata columns."""

    root = library_root.expanduser().resolve()
    target = search_index_path(root)
    if not target.is_file():
        return []
    needle = f"%{' '.join(query.split()).casefold()}%"
    with _connect(target) as connection:
        rows = connection.execute(
            """
            SELECT file_id, relative_path, title, venue, year, category, subcategory
            FROM works
            WHERE lower(
                title || ' ' || authors || ' ' || year || ' ' || venue || ' ' ||
                category || ' ' || subcategory || ' ' || tags || ' ' || summary_ko
            ) LIKE ?
            ORDER BY title
            LIMIT ?
            """,
            (needle, max(1, limit)),
        ).fetchall()
    return [
        SearchHit(
            file_id=row["file_id"],
            relative_path=row["relative_path"],
            title=row["title"],
            venue=row["venue"],
            year=row["year"],
            category=row["category"],
            subcategory=row["subcategory"],
            page=0,
            snippet="",
        )
        for row in rows
    ]
