import os
import unittest
from pathlib import Path


class SpdfStyleTests(unittest.TestCase):
    def test_caption_background_and_native_close_style(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication, QMainWindow, QTabBar
        from paper_organizer.integrations.spdf_bridge import _ensure_import_path
        from paper_organizer.ui.spdf_style import apply_spdf_caption_style
        _ensure_import_path()
        from pdfeditor.window_chrome import WindowChrome
        app = QApplication.instance() or QApplication([])
        old_sheet = app.styleSheet()
        app.setStyleSheet("QWidget { background: #f7f7f7; } QTabBar::tab { padding: 10px 18px; }")
        window = QMainWindow()
        try:
            bar = QTabBar()
            bar.setTabsClosable(True)
            bar.addTab("002-Gamer-EMBOJ-96.pdf [Reader/GPU]")
            chrome = WindowChrome(window, bar)
            window._window_chrome = chrome
            window.setMenuWidget(chrome)
            apply_spdf_caption_style(window)
            bar.addTab("Second.pdf")
            window.resize(1000, 180)
            window.show()
            app.processEvents()
            self.assertEqual(app.styleSheet(), "QWidget { background: #f7f7f7; } QTabBar::tab { padding: 10px 18px; }")
            grab = chrome.grab()
            image = grab.toImage()
            pos = chrome.drag_space.mapTo(chrome, chrome.drag_space.rect().center())
            ratio = grab.devicePixelRatio()
            self.assertEqual(image.pixelColor(int(pos.x() * ratio), int(pos.y() * ratio)).name(), "#e9edf2")
            self.assertIsNotNone(chrome._organizer_tab_style.style)
            for index in range(2):
                button = bar.tabButton(index, QTabBar.RightSide) or bar.tabButton(index, QTabBar.LeftSide)
                self.assertIsNotNone(button)
            Path("build").mkdir(exist_ok=True)
            grab.save("build/spdf-caption-qa.png")
        finally:
            window.close()
            app.setStyleSheet(old_sheet)
