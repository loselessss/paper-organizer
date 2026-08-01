"""Run the bundled sPDF RapidOCR worker without blocking the GUI thread."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread

import fitz

from paper_organizer.integrations.spdf_bridge import spdf_root
from paper_organizer.infra.redaction import redact_text
from paper_organizer.infra.secrets import sanitized_child_environment


class BackgroundOcrError(RuntimeError):
    pass


_ACTIVE_WORKERS: set[subprocess.Popen[str]] = set()
_ACTIVE_WORKERS_LOCK = Lock()
_OCR_RUN_LOCK = Lock()


def stop_active_ocr_workers() -> None:
    """Terminate OCR subprocesses owned by this app instance."""

    with _ACTIVE_WORKERS_LOCK:
        workers = tuple(_ACTIVE_WORKERS)
    for process in workers:
        _terminate_process(process)


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.SubprocessError):
            pass


def ocr_page_texts(
    pdf_path: Path,
    *,
    page_indexes: Iterable[int] | None = None,
    progress: Callable[[int, int], None] | None = None,
    background: bool = True,
    timeout_seconds: int = 900,
) -> list[str]:
    """Run only one OCR job at a time across collection and analysis workers."""

    with _OCR_RUN_LOCK:
        return _run_ocr_page_texts(
            pdf_path,
            page_indexes=page_indexes,
            progress=progress,
            background=background,
            timeout_seconds=timeout_seconds,
        )


def _run_ocr_page_texts(
    pdf_path: Path,
    *,
    page_indexes: Iterable[int] | None = None,
    progress: Callable[[int, int], None] | None = None,
    background: bool = True,
    timeout_seconds: int = 900,
) -> list[str]:
    """Return page text recognized by the isolated sPDF OCR worker."""

    source = pdf_path.expanduser().resolve()
    with fitz.open(source) as document:
        page_count = document.page_count
    if page_count < 2:
        raise BackgroundOcrError("2페이지 미만 문서는 OCR 대상에서 제외됩니다.")
    pages = (
        list(range(page_count))
        if page_indexes is None
        else sorted({index for index in page_indexes if 0 <= index < page_count})
    )
    if not pages:
        return [""] * page_count
    if getattr(sys, "frozen", False):
        command = [str(Path(sys.executable).parent / "ocr" / "spdf-ocr.exe")]
        environment = sanitized_child_environment()
    else:
        command = [sys.executable, "-m", "paper_organizer.ocr_worker_main"]
        environment = sanitized_child_environment()
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            value for value in (str(spdf_root()), existing) if value
        )
    if background:
        environment.update(
            {
                "OMP_NUM_THREADS": "1",
                "OMP_WAIT_POLICY": "PASSIVE",
                "MKL_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "PAPER_ORGANIZER_OCR_BACKGROUND": "1",
            }
        )
    else:
        environment.pop("PAPER_ORGANIZER_OCR_BACKGROUND", None)
    request = json.dumps(
        {
            "path": str(source),
            "password": "",
            "pages": pages,
            "zoom": 3.0 if background else None,
        },
        ensure_ascii=False,
    )
    process: subprocess.Popen[str] | None = None
    try:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if background:
            creation_flags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=environment,
            creationflags=creation_flags,
            bufsize=1,
        )
        with _ACTIVE_WORKERS_LOCK:
            _ACTIVE_WORKERS.add(process)
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(request + "\n")
        process.stdin.close()

        output: Queue[str | None] = Queue()

        def read_stdout() -> None:
            assert process is not None and process.stdout is not None
            for line in process.stdout:
                output.put(line)
            output.put(None)

        reader = Thread(target=read_stdout, daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout_seconds
        recognized: dict[int, list[str]] = {}
        error = ""
        finished_output = False
        while not finished_output:
            if time.monotonic() >= deadline:
                _terminate_process(process)
                raise BackgroundOcrError(
                    f"OCR worker timed out after {timeout_seconds} seconds"
                )
            try:
                line = output.get(timeout=0.2)
            except Empty:
                if process.poll() is not None and not reader.is_alive():
                    break
                continue
            if line is None:
                finished_output = True
                continue
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
            elif event.get("type") == "progress" and progress is not None:
                try:
                    progress(int(event.get("done", 0)), int(event.get("total", 0)))
                except Exception:
                    pass
            elif event.get("type") == "error":
                error = str(event.get("message") or "")
        reader.join(timeout=1)
        return_code = process.wait(timeout=max(1, int(deadline - time.monotonic())))
        stderr = process.stderr.read().strip() if process.stderr is not None else ""
        if return_code or error:
            raise BackgroundOcrError(redact_text(error or stderr or "OCR worker failed"))
        return ["\n".join(recognized.get(page, [])) for page in range(page_count)]
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackgroundOcrError(redact_text(exc)) from None
    finally:
        if process is not None:
            _terminate_process(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    try:
                        stream.close()
                    except OSError:
                        pass
            with _ACTIVE_WORKERS_LOCK:
                _ACTIVE_WORKERS.discard(process)
