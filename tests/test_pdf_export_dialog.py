"""Verify selectable PDF extraction without modifying source archives."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.core.paperpack import create_paperpack


class PdfExportDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt5.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt5 is not installed")
        cls.app = QApplication.instance() or QApplication([])

    def test_subset_selection_export_and_source_preservation(self):
        from PyQt5.QtCore import Qt
        from paper_organizer.ui.pdf_export_dialog import PdfExportDialog, _ExportWorker
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            incoming = root / "incoming"
            incoming.mkdir()
            controller = LibraryWorkflowController(root / "settings.json")
            controller.save_paths(incoming, root / "library", auto_enabled=False)
            pdf = incoming / "source.pdf"
            pdf.write_bytes(b"%PDF-1.4\nselected PDF")
            paths = [root / "library" / "papers" / f"{name}.paperpack"
                     for name in ("Alpha", "Beta")]
            for path in paths:
                create_paperpack(path, pdf, {"bibliography": {"title": path.stem}})
            originals = {path: path.read_bytes() for path in paths}
            dialog = PdfExportDialog(controller, selected_sources=[paths[1]])
            self.assertEqual(dialog.selected_sources(), [paths[1]])
            self.assertEqual(dialog.count_label.text(), "추출 대상: 1 / 2개")
            dialog.select_none_button.click()
            self.assertFalse(dialog.export_button.isEnabled())
            dialog.select_all_button.click()
            self.assertEqual(dialog.selected_sources(), paths)
            dialog.source_list.item(0).setCheckState(Qt.Unchecked)
            results, errors = [], []
            worker = _ExportWorker(dialog.selected_sources(), root / "output")
            worker.completed.connect(results.append)
            worker.failed.connect(errors.append)
            worker.run()
            self.assertEqual(errors, [])
            self.assertEqual(len(results[0].items), 1)
            self.assertEqual(len(list((root / "output").rglob("*.pdf"))), 1)
            self.assertEqual({path: path.read_bytes() for path in paths}, originals)
            dialog.close()
            default = PdfExportDialog(controller)
            self.assertEqual(default.selected_sources(), paths)
            default.close()

    def test_empty_selection_and_busy_controls(self):
        from paper_organizer.ui.pdf_export_dialog import PdfExportDialog
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            controller = LibraryWorkflowController(root / "settings.json")
            incoming = root / "incoming"
            incoming.mkdir()
            controller.save_paths(incoming, root / "library", auto_enabled=False)
            dialog = PdfExportDialog(controller)
            self.assertFalse(dialog.export_button.isEnabled())
            with patch("paper_organizer.ui.pdf_export_dialog._ExportWorker") as worker:
                dialog._start_export()
                worker.assert_not_called()
            dialog._set_controls_enabled(False)
            self.assertFalse(dialog.output_edit.isEnabled())
            self.assertFalse(dialog.source_list.isEnabled())
            dialog._set_controls_enabled(True)
            self.assertTrue(dialog.source_list.isEnabled())
            dialog.close()
