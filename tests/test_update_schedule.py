import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paper_organizer.application.update_schedule import UpdateCheckSchedule
from paper_organizer.infra.settings import load_settings


class UpdateCheckScheduleTests(unittest.TestCase):
    def test_automatic_check_is_due_only_once_per_24_hours(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            schedule = UpdateCheckSchedule(path)
            now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)

            self.assertTrue(schedule.is_due(now))
            schedule.mark_checked(now)
            self.assertFalse(schedule.is_due(now + timedelta(hours=23, minutes=59)))
            self.assertTrue(schedule.is_due(now + timedelta(days=1)))
            self.assertTrue(load_settings(path).last_update_check_at)

    def test_future_or_invalid_timestamp_does_not_disable_checks_forever(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            schedule = UpdateCheckSchedule(path)
            now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
            schedule.mark_checked(now + timedelta(days=1))

            self.assertTrue(schedule.is_due(now))

    def test_skipped_version_is_persisted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            schedule = UpdateCheckSchedule(path)
            schedule.skip_version("1.4.0")
            self.assertTrue(schedule.is_skipped("1.4.0"))
            self.assertFalse(schedule.is_skipped("1.4.1"))


if __name__ == "__main__":
    unittest.main()
