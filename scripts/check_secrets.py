"""Fail safely when tracked source files appear to contain API keys."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


KEY_PATTERNS = (
    ("Anthropic API key", re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI API key", re.compile(rb"\bsk-(?!ant-)[A-Za-z0-9_-]{20,}\b")),
)
EXCLUDED_PREFIXES = ("vendor/spdf/",)


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    names = [name for name in result.stdout.split(b"\0") if name]
    return [root / Path(name.decode("utf-8")) for name in names]


def find_potential_secrets(root: Path) -> list[tuple[Path, int, str]]:
    findings: list[tuple[Path, int, str]] = []
    for path in tracked_files(root):
        relative = path.relative_to(root).as_posix()
        if relative.startswith(EXCLUDED_PREFIXES) or not path.is_file():
            continue
        try:
            lines = path.read_bytes().splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            for label, pattern in KEY_PATTERNS:
                if pattern.search(line):
                    findings.append((path.relative_to(root), line_number, label))
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    findings = find_potential_secrets(root)
    for path, line_number, label in findings:
        print(f"Potential {label} at {path}:{line_number}; value withheld")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
