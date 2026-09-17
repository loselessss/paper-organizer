"""Create the matching application source archive and build inventory."""

from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import io
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
from urllib.parse import quote
import zipfile

from paper_organizer import __version__


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/loselessss/paper-organizer"
SPDF_REPOSITORY = "https://github.com/loselessss/sPDF"


def _git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root)


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(path.parts) and not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _package_inventory() -> list[tuple[str, str]]:
    packages = {
        (str(item.metadata.get("Name") or "").strip(), str(item.version).strip())
        for item in metadata.distributions()
    }
    return sorted((name, version) for name, version in packages if name and version)


def build_information(version: str, commit: str, spdf_commit: str) -> str:
    lines = [
        f"# Paper Organizer {version} dependency sources and build information",
        "",
        "This document accompanies the matching source ZIP and installers in the same GitHub release.",
        "이 문서는 같은 GitHub 릴리스의 대응 소스 ZIP 및 설치 파일과 함께 제공됩니다.",
        "",
        "## Source revisions",
        "",
        f"- Paper Organizer: [{commit}]({REPOSITORY}/commit/{commit})",
        f"- Bundled sPDF: [{spdf_commit}]({SPDF_REPOSITORY}/commit/{spdf_commit})",
        f"- Python: {platform.python_version()} ({platform.machine()})",
        "",
        "The source ZIP contains the tracked Paper Organizer source and the complete pinned sPDF submodule source.",
        "GitHub-generated source archives do not include submodule contents; use the attached source ZIP.",
        "",
        "## Rebuild",
        "",
        "```powershell",
        "python -m venv .venv",
        '.venv\\Scripts\\python -m pip install -e ".[gui,build]"',
        ".venv\\Scripts\\python -m unittest discover -s tests",
        "cmd /c build_installer.bat",
        "```",
        "",
        "Large GGUF model weights and optional CUDA downloads are not part of the installer or source ZIP.",
        "Pinned llama.cpp binary archive URLs and SHA-256 values are recorded in `paper_organizer/infra/llama_bundle.py`.",
        "Third-party notices and license texts are under `paper_organizer/assets/licenses` and `vendor/spdf/licenses`.",
        "Package source distributions are available from the projects linked by their package metadata on PyPI.",
        "",
        "## Build environment packages",
        "",
    ]
    lines.extend(
        f"- [{name} {package_version} source files]"
        f"(https://pypi.org/project/{quote(name, safe='')}/{quote(package_version, safe='')}/#files)"
        for name, package_version in _package_inventory()
    )
    return "\n".join(lines) + "\n"


def _copy_archive(output: zipfile.ZipFile, data: bytes, prefix: str, destination: str = "") -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        for item in source.infolist():
            if item.is_dir() or not _safe_archive_name(item.filename):
                continue
            output.writestr(prefix + destination + item.filename, source.read(item))


def create_release_source(root: Path, version: str) -> Path:
    root = Path(root).resolve()
    if version != __version__ or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Source release version must match the application version")
    commit = _git(root, "rev-parse", "HEAD").decode().strip()
    tagged = _git(root, "rev-parse", f"v{version}^{{commit}}").decode().strip()
    if tagged != commit:
        raise ValueError("Release tag must match HEAD")
    if _git(root, "status", "--porcelain", "--untracked-files=no").strip():
        raise ValueError("Release source requires a clean tracked working tree")
    spdf_root = root / "vendor" / "spdf"
    spdf_commit = _git(spdf_root, "rev-parse", "HEAD").decode().strip()
    tree_entry = _git(root, "ls-tree", "HEAD", "vendor/spdf").decode().split()
    if len(tree_entry) < 3 or tree_entry[2] != spdf_commit:
        raise ValueError("Checked-out sPDF does not match the release commit")

    document = build_information(version, commit, spdf_commit)
    output_dir = root / "Output"
    output_dir.mkdir(exist_ok=True)
    archive = output_dir / f"PaperOrganizer_Source_{version}.zip"
    document_path = output_dir / f"PaperOrganizer_Dependency_Sources_{version}.md"
    checksum_path = Path(str(archive) + ".sha256")
    for target in (archive, document_path, checksum_path):
        if target.exists():
            raise FileExistsError(target)
    prefix = f"PaperOrganizer-{version}/"
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
        _copy_archive(output, _git(root, "archive", "--format=zip", "HEAD"), prefix)
        _copy_archive(output, _git(spdf_root, "archive", "--format=zip", "HEAD"), prefix, "vendor/spdf/")
        output.writestr(prefix + "DEPENDENCY_SOURCES_AND_BUILD.md", document)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum_path.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    document_path.write_text(document, encoding="utf-8")
    return archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(create_release_source(ROOT, args.version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
