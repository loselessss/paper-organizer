"""CLI for PDF identity, paperpack interoperability, and index rebuilding."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from paper_organizer import __version__
from paper_organizer.core.document_identity import analyze_pdf_identity
from paper_organizer.core.indexer import rebuild_library_index
from paper_organizer.core.paperpack import (
    extract_paperpack_pdf,
    extract_paperpack_pdfs,
    import_pdf_to_paperpack,
    load_paperpack_content,
    load_paperpack_metadata,
    verify_paperpack,
)


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


def _load_json_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _paperpack_create(
    pdf: Path,
    metadata_path: Path,
    output: Path,
    content_path: Path | None,
    *,
    remove_source: bool,
    confirm_remove_source: bool,
) -> int:
    if remove_source and not confirm_remove_source:
        raise ValueError(
            "--remove-source를 사용하려면 --confirm-remove-source도 지정해야 합니다"
        )
    metadata = _load_json_object(metadata_path)
    content = _load_json_object(content_path) if content_path else None
    result = import_pdf_to_paperpack(
        output,
        pdf,
        metadata,
        content=content,
        remove_source=remove_source,
    )
    suffix = " (입력 PDF 제거됨)" if result.source_removed else ""
    print(f"paperpack 생성 완료: {result.paperpack.path}{suffix}")
    return 0


def _paperpack_inspect(path: Path) -> int:
    info = verify_paperpack(path)
    print(
        json.dumps(
            {
                "path": str(info.path),
                "original_name": info.original_name,
                "pdf_sha256": info.pdf_sha256,
                "pdf_size": info.pdf_size,
                "revision": info.revision,
                "created_at": info.created_at,
                "updated_at": info.updated_at,
                "metadata": load_paperpack_metadata(path),
                "content": load_paperpack_content(path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _paperpack_extract(path: Path, output: Path) -> int:
    extracted = extract_paperpack_pdf(path, output)
    print(f"PDF 추출 완료: {extracted}")
    return 0


def _collect_paperpacks(inputs: list[Path], *, recursive: bool) -> list[Path]:
    collected: list[Path] = []
    seen: set[Path] = set()
    for value in inputs:
        path = value.expanduser().resolve()
        candidates = (
            sorted(path.rglob("*.paperpack") if recursive else path.glob("*.paperpack"))
            if path.is_dir()
            else [path]
        )
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                collected.append(resolved)
    return collected


def _paperpack_extract_many(
    inputs: list[Path],
    output_dir: Path,
    *,
    recursive: bool,
    remove_source: bool,
    confirm_remove_source: bool,
) -> int:
    if remove_source and not confirm_remove_source:
        raise ValueError(
            "--remove-source를 사용하려면 --confirm-remove-source도 지정해야 합니다"
        )
    sources = _collect_paperpacks(inputs, recursive=recursive)
    result = extract_paperpack_pdfs(
        sources,
        output_dir,
        remove_sources=remove_source,
    )
    for item in result.items:
        suffix = " (paperpack 제거됨)" if item.source_removed else ""
        print(f"{item.paperpack_path} -> {item.pdf_path}{suffix}")
    print(f"PDF 일괄 추출 완료: {len(result.items)}개")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paper-organizer")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    identity = subparsers.add_parser("identity", help="PDF 동일성 지문을 출력합니다")
    identity.add_argument("pdf", type=Path)

    reindex = subparsers.add_parser(
        "reindex", help="paperpack/legacy sidecar로 인덱스를 재구축합니다"
    )
    reindex.add_argument("library", type=Path)

    paperpack = subparsers.add_parser("paperpack", help=".paperpack 파일을 관리합니다")
    paperpack_commands = paperpack.add_subparsers(
        dest="paperpack_command", required=True
    )
    create = paperpack_commands.add_parser("create", help="PDF와 JSON으로 생성합니다")
    create.add_argument("pdf", type=Path)
    create.add_argument("metadata", type=Path)
    create.add_argument("output", type=Path)
    create.add_argument("--content", type=Path)
    create.add_argument(
        "--remove-source",
        action="store_true",
        help="paperpack 검증 성공 후 입력 PDF를 제거합니다",
    )
    create.add_argument(
        "--confirm-remove-source",
        action="store_true",
        help="입력 PDF 제거를 명시적으로 재확인합니다",
    )
    inspect = paperpack_commands.add_parser("inspect", help="검증하고 내용을 출력합니다")
    inspect.add_argument("paperpack", type=Path)
    extract = paperpack_commands.add_parser("extract", help="내장 PDF를 추출합니다")
    extract.add_argument("paperpack", type=Path)
    extract.add_argument("output", type=Path)
    extract_many = paperpack_commands.add_parser(
        "extract-many", help="여러 paperpack 또는 폴더에서 PDF를 일괄 추출합니다"
    )
    extract_many.add_argument("inputs", type=Path, nargs="+")
    extract_many.add_argument("--output-dir", type=Path, required=True)
    extract_many.add_argument(
        "--recursive", action="store_true", help="입력 폴더의 하위 폴더도 탐색합니다"
    )
    extract_many.add_argument(
        "--remove-source",
        action="store_true",
        help="전체 PDF 검증 성공 후 원본 paperpack을 제거합니다",
    )
    extract_many.add_argument(
        "--confirm-remove-source",
        action="store_true",
        help="원본 제거를 명시적으로 재확인합니다",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "identity":
        return _identity(args.pdf)
    if args.command == "reindex":
        return _reindex(args.library)
    if args.command == "paperpack":
        if args.paperpack_command == "create":
            return _paperpack_create(
                args.pdf,
                args.metadata,
                args.output,
                args.content,
                remove_source=args.remove_source,
                confirm_remove_source=args.confirm_remove_source,
            )
        if args.paperpack_command == "inspect":
            return _paperpack_inspect(args.paperpack)
        if args.paperpack_command == "extract":
            return _paperpack_extract(args.paperpack, args.output)
        if args.paperpack_command == "extract-many":
            return _paperpack_extract_many(
                args.inputs,
                args.output_dir,
                recursive=args.recursive,
                remove_source=args.remove_source,
                confirm_remove_source=args.confirm_remove_source,
            )
    raise AssertionError(f"Unhandled command: {args.command}")
