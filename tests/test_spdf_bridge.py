import unittest

from paper_organizer.integrations.spdf_bridge import (
    _attach_selection,
    _normalized_selection,
    spdf_available,
    spdf_version,
)


class SpdfBridgeTests(unittest.TestCase):
    def test_submodule_and_version_are_detected_without_importing_pyqt(self):
        self.assertTrue(spdf_available())
        self.assertEqual(spdf_version(), "1.7.3")

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
