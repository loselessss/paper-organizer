import unittest

from paper_organizer.integrations.spdf_bridge import spdf_available, spdf_version


class SpdfBridgeTests(unittest.TestCase):
    def test_submodule_and_version_are_detected_without_importing_pyqt(self):
        self.assertTrue(spdf_available())
        self.assertRegex(spdf_version() or "", r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
