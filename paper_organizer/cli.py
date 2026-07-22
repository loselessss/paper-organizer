"""Development CLI for inspecting PDF identity and rebuilding indexes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from paper_organizer import __version__
from paper_organizer.core.document_identity import analyze_pdf_identity
from paper_organizer.core.indexer import rebuild_library_index


def _identity(path: Path) -> int:
    identity = analyze_pdf_identity(path)
    print(json.dumps(identity.to_dict(), ensure_ascii=False, indent=2))
    return 0


def _reindex(path: Path) -> int:
    index, problems = rebuild_library_index(path)
    print(
        f"색인 완료: 논문 {index['work_count']}건, 파일 {index['file_count']}개, "
        f"오류 {len(problems)}건"
    )
    for problem in problems:
        print(f"- {problem.path}: {problem.message}")
    return 0 if not problems else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-organizer")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    identity = subparsers.add_parser("identity", help="PDF 동일성 지문을 출력합니다")
    identity.add_argument("pdf", type=Path)

    reindex = subparsers.add_parser("reindex", help="sidecar JSON으로 인덱스를 재구축합니다")
    reindex.add_argument("library", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "identity":
        return _identity(args.pdf)
    if args.command == "reindex":
        return _reindex(args.library)
    raise AssertionError(f"Unhandled command: {args.command}")
