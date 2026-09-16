import unittest
import tempfile
import types
from pathlib import Path
from unittest.mock import Mock, patch

from paper_organizer.integrations.spdf_bridge import (
    _attach_selection,
    _normalized_selection,
    spdf_available,
    spdf_version,
)


class SpdfBridgeTests(unittest.TestCase):
    def test_open_uses_gpu_reader_policy_with_updates_disabled(self):
        from paper_organizer.integrations import spdf_bridge
        factory = Mock()
        widgets = types.ModuleType("PyQt5.QtWidgets")
        widgets.QApplication = Mock()
        app = types.ModuleType("pdfeditor.app")
        app.new_window = factory
        style = types.ModuleType("paper_organizer.ui.spdf_style")
        style.apply_spdf_caption_style = Mock()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "test.pdf"
            path.write_bytes(b"%PDF-1.4")
            with patch.dict("sys.modules", {"PyQt5.QtWidgets": widgets, "pdfeditor.app": app, "paper_organizer.ui.spdf_style": style}), patch.object(spdf_bridge, "_windows", []), patch.object(spdf_bridge, "_ensure_import_path"), patch.object(spdf_bridge, "_attach_selection"):
                spdf_bridge.open_pdf(path)
        style.apply_spdf_caption_style.assert_called_once_with(factory.return_value)
        factory.assert_called_once_with(workspace_mode="reader", read_only=True,
            annotations_enabled=False, updates_enabled=False)

    def test_gpu_worker_is_dispatched_before_gui_startup(self):
        from paper_organizer.gui import main
        worker = types.ModuleType("pdfeditor.gpu_scene_worker")
        worker.main = Mock(return_value=0)
        with patch.dict("sys.modules", {"pdfeditor.gpu_scene_worker": worker}), patch("sys.argv", ["app", "--gpu-scene-worker", "snapshot", "result"]), patch("paper_organizer.integrations.spdf_bridge._ensure_import_path"):
            self.assertEqual(main(), 0)
        worker.main.assert_called_once_with(["snapshot", "result"])

    def test_submodule_and_version_are_detected_without_importing_pyqt(self):
        self.assertTrue(spdf_available())
        self.assertRegex(spdf_version() or "", r"^\d+\.\d+\.\d+$")

    def test_public_selection_payload_is_normalized_without_qt_objects(self):
        class Payload:
            text = "selected text"
            pdf_page = 2
            bounding_boxes = ((1, 2, 30, 40),)
            document_id = "paper-1"
            document_path = "paper.pdf"
            requires_ocr = False

        value = _normalized_selection(Payload())
        self.assertEqual(value.text, "selected text")
        self.assertEqual(value.pdf_page, 2)
        self.assertEqual(value.bounding_boxes, ((1.0, 2.0, 30.0, 40.0),))

    def test_plain_open_removes_previous_ai_selection_callback(self):
        class Signal:
            def __init__(self):
                self.callbacks = []

            def connect(self, callback):
                self.callbacks.append(callback)

            def disconnect(self, callback):
                self.callbacks.remove(callback)

        class Tab:
            def __init__(self):
                self.selection_changed = Signal()
                self.document_id = ""

            def set_selection_document_id(self, value):
                self.document_id = value

        tab = Tab()
        _attach_selection(tab, "paper-1", lambda _value: None)
        self.assertEqual(len(tab.selection_changed.callbacks), 1)

        _attach_selection(tab, "paper-1", None)

        self.assertEqual(tab.selection_changed.callbacks, [])
        self.assertEqual(tab._paper_organizer_selection_callbacks, [])


if __name__ == "__main__":
    unittest.main()
