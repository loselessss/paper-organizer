import os
import sys
import types
import unittest
from unittest.mock import patch

from paper_organizer.ocr_worker_main import _limit_background_runtime


class OcrWorkerTests(unittest.TestCase):
    def test_background_runtime_limits_onnx_and_opencv_threads(self):
        calls = []
        cv_threads = []

        def rapid_ocr(config_path=None, params=None):
            calls.append((config_path, params))
            return object()

        rapidocr = types.SimpleNamespace(RapidOCR=rapid_ocr)
        cv2 = types.SimpleNamespace(setNumThreads=cv_threads.append)
        with patch.dict(
            sys.modules,
            {"rapidocr": rapidocr, "cv2": cv2},
        ), patch.dict(
            os.environ,
            {"PAPER_ORGANIZER_OCR_BACKGROUND": "1"},
        ):
            _limit_background_runtime()
            rapidocr.RapidOCR(params={"Global.text_score": 0.6})

        self.assertEqual(cv_threads, [1])
        self.assertEqual(calls[0][1]["Global.text_score"], 0.6)
        self.assertEqual(
            calls[0][1]["EngineConfig.onnxruntime.intra_op_num_threads"], 1
        )
        self.assertEqual(
            calls[0][1]["EngineConfig.onnxruntime.inter_op_num_threads"], 1
        )

    def test_foreground_runtime_keeps_rapidocr_defaults(self):
        original = object()
        rapidocr = types.SimpleNamespace(RapidOCR=original)
        with patch.dict(sys.modules, {"rapidocr": rapidocr}), patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            _limit_background_runtime()

        self.assertIs(rapidocr.RapidOCR, original)


if __name__ == "__main__":
    unittest.main()
