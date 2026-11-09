"""Paper Organizer entry point for the isolated sPDF OCR worker."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _limit_background_runtime() -> None:
    if os.environ.get("PAPER_ORGANIZER_OCR_BACKGROUND") != "1":
        return

    import rapidocr

    original = rapidocr.RapidOCR

    def limited_rapidocr(config_path=None, params=None):
        values = dict(params or {})
        values.update(
            {
                "EngineConfig.onnxruntime.intra_op_num_threads": 1,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
        return original(config_path=config_path, params=values)

    rapidocr.RapidOCR = limited_rapidocr
    try:
        import cv2

        cv2.setNumThreads(1)
    except (ImportError, AttributeError):
        pass


def main() -> int:
    _limit_background_runtime()
    if getattr(sys, "frozen", False):
        from pdfeditor import ocr_page_source
        # The hosted OCR process renders PDFs through the organizer executable.
        executable = Path(sys.executable).resolve().parent.parent / "PaperOrganizer.exe"
        ocr_page_source.renderer_command = lambda: [str(executable), "--ocr-render-worker"]
    from pdfeditor.ocr_subprocess import main as spdf_ocr_main

    return spdf_ocr_main()


if __name__ == "__main__":
    raise SystemExit(main())
