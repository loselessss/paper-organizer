import unittest

from scripts.extract_release_notes import extract_release_notes, render_release_notes


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

    def test_full_notes_point_installers_and_sources_to_same_assets(self):
        changelog = "## [2.5.0] - 2026-09-16\n\n- Project support\n"
        notes = render_release_notes(changelog, "2.5.0")
        self.assertIn("Paper Organizer 2.5.0 — 2026-09-16", notes)
        self.assertIn("matching source ZIP", notes)
        self.assertIn("함께 받을 수 있습니다", notes)

    def test_full_notes_include_previous_feature_highlights(self):
        changelog = "## [2.5.2] - 2026-10-27\n\n- Patch fix\n"
        release_notes = (
            "# Paper Organizer 2.5.2 — 2026-10-27\n\n"
            "## 2.5.2 변경\n\n- Patch fix\n\n"
            "## 2.5.0 주요 변경\n\n- Project support\n"
        )
        notes = render_release_notes(changelog, "2.5.2", release_notes)
        self.assertEqual(notes.count("# Paper Organizer 2.5.2"), 1)
        self.assertIn("## 2.5.0 주요 변경", notes)
        self.assertIn("matching source ZIP", notes)

    def test_full_notes_reject_mismatched_version(self):
        changelog = "## [2.5.2] - 2026-10-27\n\n- Patch fix\n"
        with self.assertRaisesRegex(ValueError, "버전과 날짜"):
            render_release_notes(
                changelog, "2.5.2", "# Paper Organizer 2.5.1 — 2026-09-27\n\n- Old notes"
            )
