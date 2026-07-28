import unittest

from scripts.extract_release_notes import extract_release_notes


class ReleaseNotesTests(unittest.TestCase):
    def test_extracts_only_requested_version_body(self):
        changelog = """# 변경 기록

## [1.4.2] - 2026-07-28

### 추가

- 상세 변경 내용

## [1.4.1] - 2026-07-28

- 이전 변경
"""
        notes = extract_release_notes(changelog, "v1.4.2")

        self.assertIn("### 추가", notes)
        self.assertIn("상세 변경 내용", notes)
        self.assertNotIn("1.4.1", notes)
        self.assertNotIn("이전 변경", notes)

    def test_missing_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "찾지 못했습니다"):
            extract_release_notes("## [1.0.0]\n\n- first\n", "2.0.0")
