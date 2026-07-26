"""Run the bundled sPDF RapidOCR worker without blocking the GUI thread."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import fitz

from paper_organizer.integrations.spdf_bridge import spdf_root


class BackgroundOcrError(RuntimeError):
    pass


def ocr_page_texts(pdf_path: Path, *, timeout_seconds: int = 900) -> list[str]:
    """Return page text recognized by the isolated sPDF OCR worker."""

    source = pdf_path.expanduser().resolve()
    with fitz.open(source) as document:
        pages = list(range(document.page_count))
    if getattr(sys, "frozen", False):
        command = [str(Path(sys.executable).parent / "ocr" / "spdf-ocr.exe")]
        environment = None
    else:
        command = [sys.executable, "-m", "pdfeditor.ocr_subprocess"]
        environment = dict(os.environ)
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(spdf_root()), existing) if value
        )
    request = json.dumps(
        {"path": str(source), "password": "", "pages": pages, "zoom": None},
        ensure_ascii=False,
    )
    try:
        completed = subprocess.run(
            command,
            input=request + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_seconds,
            check=False,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackgroundOcrError(str(exc)) from None
    recognized: dict[int, list[str]] = {}
    error = ""
    for line in completed.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "page":
            recognized[int(event["page"])] = [
                str(item[4]).strip()
                for item in event.get("items", [])
                if len(item) >= 5 and str(item[4]).strip()
            ]
        elif event.get("type") == "error":
            error = str(event.get("message") or "")
    if completed.returncode or error:
        raise BackgroundOcrError(error or completed.stderr.strip() or "OCR worker failed")
    return ["\n".join(recognized.get(page, [])) for page in pages]
