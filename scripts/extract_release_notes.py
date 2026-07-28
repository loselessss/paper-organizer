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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=DEFAULT_CHANGELOG,
    )
    args = parser.parse_args()
    text = args.changelog.read_text(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(extract_release_notes(text, args.version), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
