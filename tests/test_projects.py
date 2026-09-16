import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paper_organizer.application.library_workflow import LibraryWorkflowController
from paper_organizer.core.paperpack import create_paperpack, load_paperpack_metadata, update_paperpack


def project_fixture(root):
    controller = LibraryWorkflowController(root / "settings.json")
    incoming = root / "incoming"
    incoming.mkdir()
    library = root / "library"
    controller.save_paths(incoming, library, auto_enabled=False)
    source = incoming / "sample.pdf"
    source.write_bytes(b"%PDF-1.4\nproject fixture")
    for title in ("Alpha", "Beta"):
        create_paperpack(library / "papers" / f"{title}.paperpack", source, {
            "identity": {"work_id": title, "file_id": title},
            "file": {"relative_path": f"papers/{title}.paperpack"},
            "bibliography": {"title": title, "authors": ["Author"]},
            "curation": {"field_sources": {"bibliography.title": "user"}},
        })
    return controller


class ProjectTests(unittest.TestCase):
    def test_memberships_persist_without_mutating_bibliography(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            controller = project_fixture(root)
            first = controller.save_project("First", "Notes")
            second = controller.save_project("Second")
            entry = controller.list_library()[0]
            before = load_paperpack_metadata(entry.sidecar_path)
            self.assertEqual(controller.set_project_membership([entry], first, included=True), (1, ()))
            self.assertEqual(controller.set_project_membership([entry], first, included=True), (0, ()))
            controller.set_project_membership([entry], second, included=True)
            reopened = LibraryWorkflowController(root / "settings.json")
            stored = reopened.list_library()[0].record
            self.assertEqual(set(stored["curation"]["project_ids"]), {first, second})
            self.assertEqual(stored["bibliography"], before["bibliography"])
            self.assertEqual(stored["curation"]["field_sources"], before["curation"]["field_sources"])
            reopened.set_project_membership([entry], first, included=False)
            self.assertEqual(reopened.list_library()[0].record["curation"]["project_ids"], [second])

    def test_rename_empty_project_and_delete_leave_documents_intact(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = project_fixture(Path(temp))
            key = controller.save_project("Empty")
            controller.save_project("Renamed", "Description", project_id=key)
            self.assertEqual(controller.list_projects()[0]["name"], "Renamed")
            entries = controller.list_library()
            controller.set_project_membership(entries, key, included=True)
            before = {e.sidecar_path: e.sidecar_path.read_bytes() for e in entries}
            controller.delete_project(key)
            self.assertEqual(controller.list_projects(), [])
            self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))
            self.assertNotEqual(controller.save_project("Renamed"), key)

    def test_invalid_names_and_partial_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            controller = project_fixture(Path(temp))
            key = controller.save_project("Project")
            for name in ("", " project ", "x" * 81):
                with self.assertRaises(RuntimeError):
                    controller.save_project(name)
            entries = controller.list_library()
            def fail_one(path, *args, **kwargs):
                if path == entries[0].sidecar_path:
                    raise OSError("busy")
                return update_paperpack(path, *args, **kwargs)
            with patch("paper_organizer.application.library_workflow.update_paperpack", side_effect=fail_one):
                count, errors = controller.set_project_membership(entries, key, included=True)
            self.assertEqual(count, 1)
            self.assertEqual(len(errors), 1)


class ProjectUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        try:
            from PyQt5.QtWidgets import QApplication
        except ImportError:
            raise unittest.SkipTest("PyQt5 is not installed")
        cls.app = QApplication.instance() or QApplication([])

    def test_click_filters_sorted_selection_and_removal(self):
        from PyQt5.QtCore import Qt
        from paper_organizer.ui.library_workflow_widget import LibraryWidget
        with tempfile.TemporaryDirectory() as temp:
            controller = project_fixture(Path(temp))
            key = controller.save_project("Project")
            widget = LibraryWidget(controller)
            widget.resize(1200, 760)
            widget.show()
            self.app.processEvents()
            search_x = widget.search_edit.x()
            widget.table.sortItems(0, Qt.DescendingOrder)
            widget.table.selectRow(0)
            widget._set_selected_project(key, True)
            self.assertEqual(widget.project_membership_label.text(), "Project")
            second = controller.save_project("Second")
            widget._set_selected_project(second, True)
            self.assertEqual(widget.project_membership_label.text(), "Project · Second")
            widget.project_sidebar.list.setCurrentRow(1)
            self.app.processEvents()
            self.assertEqual(widget.search_edit.x(), search_x)
            controller.save_project("Long project name " * 4, project_id=key)
            widget.refresh()
            self.app.processEvents()
            self.assertEqual(widget.search_edit.x(), search_x)
            self.assertEqual(widget.table.rowCount(), 1)
            self.assertEqual(widget.table.item(0, 0).text(), "Beta")
            widget.search_edit.setText("Alpha")
            widget.refresh()
            self.assertEqual(widget.table.rowCount(), 0)
            widget.search_edit.clear()
            self.assertEqual(widget.table.rowCount(), 1)
            widget._set_selected_project(key, False)
            self.assertEqual(widget.table.rowCount(), 0)
            widget.project_sidebar.list.setCurrentRow(0)
            self.assertEqual(widget.table.rowCount(), 2)
            widget.close()
