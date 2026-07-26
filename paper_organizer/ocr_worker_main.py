"""Paper Organizer entry point for the isolated sPDF OCR worker."""

from __future__ import annotations

import os


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
    from pdfeditor.ocr_subprocess import main as spdf_ocr_main

    return spdf_ocr_main()


if __name__ == "__main__":
    raise SystemExit(main())
