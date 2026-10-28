#!/usr/bin/env python3
"""Extract one version section from the project changelog."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANGELOG = REPOSITORY_ROOT / "CHANGELOG.md"


def extract_release_notes(changelog: str, version: str) -> str:
    normalized = version.strip().removeprefix("v")
    if not normalized:
        raise ValueError("릴리스 버전이 비어 있습니다.")
    heading = re.compile(
        rf"^## \[{re.escape(normalized)}\](?:\s+-\s+.*)?\s*$",
        re.MULTILINE,
    )
    match = heading.search(changelog)
    if match is None:
        raise ValueError(f"CHANGELOG.md에서 {normalized} 항목을 찾지 못했습니다.")
    next_heading = re.search(
        r"^## \[[^\]]+\](?:\s+-\s+.*)?\s*$",
        changelog[match.end() :],
        re.MULTILINE,
    )
    end = (
        match.end() + next_heading.start()
        if next_heading is not None
        else len(changelog)
    )
    body = changelog[match.end() : end].strip()
    if not body:
        raise ValueError(f"{normalized} 릴리스 변경 내용이 비어 있습니다.")
    return body + "\n"


def render_release_notes(
    changelog: str, version: str, release_notes: str | None = None
) -> str:
    normalized = version.strip().removeprefix("v")
    date_match = re.search(
        rf"^## \[{re.escape(normalized)}\]\s+-\s+([^\s]+)\s*$",
        changelog,
        re.MULTILINE,
    )
    if date_match is None:
        raise ValueError(f"CHANGELOG.md에서 {normalized} 릴리스 날짜를 찾지 못했습니다.")
    heading = f"# Paper Organizer {normalized} — {date_match.group(1)}"
    if release_notes is None:
        details = extract_release_notes(changelog, normalized).strip()
    else:
        lines = release_notes.strip().splitlines()
        if not lines or lines[0].strip() != heading:
            raise ValueError("RELEASE_NOTES.md의 버전과 날짜가 변경 기록과 다릅니다.")
        details = "\n".join(lines[1:]).strip()
        if not details:
            raise ValueError("RELEASE_NOTES.md의 변경 내용이 비어 있습니다.")
    intro = (
        "Windows installers and their matching source ZIP, source checksum, and "
        "dependency/build information are available together under Assets.\n\n"
        "Windows 설치 파일과 대응 소스 ZIP, 소스 체크섬, 의존성·빌드 정보는 "
        "아래 Assets에서 함께 받을 수 있습니다."
    )
    return (
        f"{heading}\n\n{intro}\n\n{details}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--release-notes", type=Path)
    parser.add_argument(
        "--changelog",
        type=Path,
        default=DEFAULT_CHANGELOG,
    )
    args = parser.parse_args()
    if args.release_notes is not None and not args.full:
        parser.error("--release-notes requires --full")
    text = args.changelog.read_text(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    release_notes = (
        args.release_notes.read_text(encoding="utf-8")
        if args.release_notes is not None else None
    )
    notes = (
        render_release_notes(text, args.version, release_notes)
        if args.full else extract_release_notes(text, args.version)
    )
    print(notes, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
