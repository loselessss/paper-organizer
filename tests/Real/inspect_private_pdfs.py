"""Extract private benchmark PDFs into ignored local working files."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parent
WORK = ROOT / "work"


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.stem}-", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main() -> None:
    manifest_path = WORK / "manifest.private.json"
    existing_rows = []
    if manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing_rows = list(existing.get("documents", []))
    by_source = {
        str(row.get("private_source")): str(row.get("document_id"))
        for row in existing_rows
        if row.get("private_source") and row.get("document_id")
    }
    by_hash = {
        str(row.get("sha256")): str(row.get("document_id"))
        for row in existing_rows
        if row.get("sha256") and row.get("document_id")
    }
    assigned = set(by_source.values()) | set(by_hash.values())
    next_number = max(
        (
            int(value.removeprefix("REAL-"))
            for value in assigned
            if value.startswith("REAL-") and value.removeprefix("REAL-").isdigit()
        ),
        default=0,
    ) + 1
    rows = []
    for pdf_path in sorted(ROOT.glob("*.pdf")):
        digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        document_id = by_source.get(pdf_path.name) or by_hash.get(digest)
        if not document_id:
            document_id = f"REAL-{next_number:03d}"
            next_number += 1
        document = fitz.open(pdf_path)
        try:
            pages = [page.get_text("text") or "" for page in document]
            pixmap = document[0].get_pixmap(
                matrix=fitz.Matrix(1.5, 1.5),
                alpha=False,
            )
        finally:
            document.close()
        text = "\n\n".join(
            f"[PDF PAGE {page_index}]\n{page_text}"
            for page_index, page_text in enumerate(pages, 1)
        )
        atomic_text(WORK / "extracted" / f"{document_id}.txt", text)
        render_path = WORK / "render" / f"{document_id}-page1.png"
        atomic_bytes(render_path, pixmap.tobytes("png"))
        rows.append(
            {
                "document_id": document_id,
                "private_source": pdf_path.name,
                "sha256": digest,
                "page_count": len(pages),
                "text_characters": sum(len(page) for page in pages),
                "needs_ocr": sum(len(page.strip()) for page in pages) < 500,
            }
        )
    rows.sort(key=lambda row: row["document_id"])
    atomic_text(
        manifest_path,
        json.dumps({"documents": rows}, ensure_ascii=False, indent=2) + "\n",
    )
    for row in rows:
        print(
            f"{row['document_id']} pages={row['page_count']} "
            f"text_chars={row['text_characters']} needs_ocr={row['needs_ocr']}"
        )


if __name__ == "__main__":
    main()
