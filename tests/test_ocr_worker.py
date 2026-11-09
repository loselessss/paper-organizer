import os
import sys
import types
import unittest
from pathlib import Path
import subprocess
from unittest.mock import Mock, patch

from paper_organizer.ocr_worker_main import _limit_background_runtime


class OcrWorkerTests(unittest.TestCase):
    def test_frozen_ocr_uses_host_for_pdf_raster(self):
        from paper_organizer.ocr_worker_main import main
        page_source = types.ModuleType("pdfeditor.ocr_page_source")
        package = types.ModuleType("pdfeditor")
        package.ocr_page_source = page_source
        ocr = types.ModuleType("pdfeditor.ocr_subprocess")
        ocr.main = Mock(return_value=0)
        executable = Path("installed/ocr/leaflet-ocr.exe").resolve()
        with patch.dict(sys.modules, {"pdfeditor": package,
                                     "pdfeditor.ocr_page_source": page_source,
                                     "pdfeditor.ocr_subprocess": ocr}), patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", str(executable)), patch("paper_organizer.ocr_worker_main._limit_background_runtime"):
            self.assertEqual(main(), 0)
            self.assertEqual(page_source.renderer_command(),
                             [str(executable.parent.parent / "PaperOrganizer.exe"), "--ocr-render-worker"])

    def test_large_worker_diagnostics_do_not_block_page_results(self):
        from paper_organizer.application import background_ocr
        real_popen = subprocess.Popen
        commands = []

        def launch(command, **kwargs):
            commands.append(command)
            script = ("import sys,json; sys.stdin.readline(); "
                      "sys.stderr.write('x'*200000); sys.stderr.flush(); "
                      "print(json.dumps({'type':'done'}),flush=True)")
            return real_popen([sys.executable, "-c", script], **kwargs)

        document = Mock()
        document.__enter__ = Mock(return_value=types.SimpleNamespace(page_count=2))
        document.__exit__ = Mock(return_value=False)
        with patch.object(background_ocr.fitz, "open", return_value=document), patch.object(background_ocr.subprocess, "Popen", side_effect=launch), patch.object(sys, "frozen", True, create=True):
            self.assertEqual(background_ocr._run_ocr_page_texts(Path("unused.pdf"), timeout_seconds=15), ["", ""])
        self.assertEqual(Path(commands[0][0]).name, "leaflet-ocr.exe")

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
