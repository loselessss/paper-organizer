import json
import tempfile
import unittest
from pathlib import Path

from paper_organizer.infra.settings import (
    AppSettings,
    load_settings,
    ollama_model_for_purpose,
    save_settings,
    settings_for_summary_purpose,
)


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            expected = AppSettings(
                input_dir=str(Path(temp) / "input"),
                library_root=str(Path(temp) / "library"),
                auto_enabled=True,
                selected_model="qwen3:4b",
            )
            save_settings(expected, path)
            self.assertEqual(load_settings(path), expected)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["resource_profile"], "eco")
            self.assertEqual(saved["summary_provider"], "ollama")
            self.assertNotIn("api_key", saved)
            self.assertNotIn("OPENAI_API_KEY", path.read_text(encoding="utf-8"))

    def test_same_input_and_library_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = AppSettings(input_dir=temp, library_root=temp)
            with self.assertRaises(ValueError):
                settings.validate()

    def test_invalid_json_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            path.write_text("not json", encoding="utf-8")
            self.assertEqual(load_settings(path), AppSettings())

    def test_unknown_summary_provider_is_rejected(self):
        settings = AppSettings(summary_provider="unknown")
        with self.assertRaises(ValueError):
            settings.validate()

    def test_high_throughput_cloud_profile_is_supported(self):
        settings = AppSettings(
            cloud_request_profile="high_throughput",
            cloud_max_parallel_requests=8,
            cloud_monthly_budget_usd=0,
        )
        settings.validate()

    def test_cloud_parallelism_is_bounded(self):
        settings = AppSettings(cloud_max_parallel_requests=17)
        with self.assertRaises(ValueError):
            settings.validate()

    def test_scan_interval_is_adjustable_but_bounded(self):
        AppSettings(scan_interval_seconds=300).validate()
        with self.assertRaises(ValueError):
            AppSettings(scan_interval_seconds=4).validate()
        with self.assertRaises(ValueError):
            AppSettings(scan_interval_seconds=3601).validate()

    def test_lifecycle_preferences_are_validated(self):
        AppSettings(
            first_run_completed=True,
            start_with_windows=True,
            close_behavior="background",
        ).validate()
        with self.assertRaises(ValueError):
            AppSettings(close_behavior="ask").validate()

    def test_hardware_snapshot_must_be_a_json_object(self):
        with self.assertRaises(ValueError):
            AppSettings(hardware_profile=[]).validate()

    def test_managed_ollama_models_must_be_unique_names(self):
        AppSettings(managed_ollama_models=["qwen3:4b"]).validate()
        with self.assertRaises(ValueError):
            AppSettings(managed_ollama_models=["qwen3:4b", "QWEN3:4B"]).validate()

    def test_background_analysis_is_enabled_by_default(self):
        self.assertTrue(AppSettings().background_analysis_enabled)

    def test_ollama_residency_settings_are_validated(self):
        AppSettings(
            ollama_residency_mode="30m",
            ollama_resident_model="qwen3:4b",
        ).validate()
        with self.assertRaises(ValueError):
            AppSettings(ollama_residency_mode="forever").validate()

    def test_background_and_manual_models_are_selected_independently(self):
        settings = AppSettings(
            selected_model="legacy:2b",
            background_model="qwen3:1.7b",
            manual_model="qwen3:4b",
            background_model_resident=True,
        )

        background = settings_for_summary_purpose(settings, "background")
        manual = settings_for_summary_purpose(settings, "manual")

        self.assertEqual(
            ollama_model_for_purpose(settings, "background"),
            "qwen3:1.7b",
        )
        self.assertEqual(
            ollama_model_for_purpose(settings, "manual"),
            "qwen3:4b",
        )
        self.assertEqual(background.selected_model, "qwen3:1.7b")
        self.assertEqual(background.ollama_residency_mode, "always")
        self.assertEqual(manual.selected_model, "qwen3:4b")
        self.assertEqual(manual.ollama_residency_mode, "unload")

    def test_legacy_single_model_remains_the_fallback_for_both_purposes(self):
        settings = AppSettings(selected_model="qwen3:1.7b")

        self.assertEqual(
            ollama_model_for_purpose(settings, "background"),
            "qwen3:1.7b",
        )
        self.assertEqual(
            ollama_model_for_purpose(settings, "manual"),
            "qwen3:1.7b",
        )

    def test_model_split_settings_are_validated(self):
        with self.assertRaises(ValueError):
            AppSettings(background_model_resident="yes").validate()
        with self.assertRaises(ValueError):
            AppSettings(background_model=[]).validate()
        with self.assertRaises(ValueError):
            AppSettings(manual_model=[]).validate()

    def test_ollama_igpu_setting_is_boolean(self):
        self.assertTrue(AppSettings().ollama_force_igpu)
        AppSettings(ollama_force_igpu=True).validate()
        with self.assertRaises(ValueError):
            AppSettings(ollama_force_igpu="yes").validate()

    def test_ollama_model_benchmarks_are_json_objects(self):
        AppSettings(
            ollama_model_benchmarks={"qwen3:1.7b": {"processor": "GPU"}}
        ).validate()
        with self.assertRaises(ValueError):
            AppSettings(ollama_model_benchmarks={"qwen3:1.7b": "GPU"}).validate()

    def test_summary_language_is_validated(self):
        AppSettings(summary_language="ko").validate()
        AppSettings(summary_language="source").validate()
        with self.assertRaises(ValueError):
            AppSettings(summary_language="automatic").validate()

    def test_summary_timeout_is_generous_and_bounded(self):
        self.assertEqual(AppSettings().summary_timeout_seconds, 900)
        AppSettings(summary_timeout_seconds=3600).validate()
        with self.assertRaises(ValueError):
            AppSettings(summary_timeout_seconds=59).validate()
        with self.assertRaises(ValueError):
            AppSettings(summary_timeout_seconds=3601).validate()

    def test_analysis_intervals_are_independently_configurable(self):
        settings = AppSettings(
            automatic_analysis_interval_seconds=45,
            manual_analysis_interval_seconds=3,
        )
        settings.validate()
        self.assertEqual(settings.automatic_analysis_interval_seconds, 45)
        self.assertEqual(settings.manual_analysis_interval_seconds, 3)
        with self.assertRaises(ValueError):
            AppSettings(automatic_analysis_interval_seconds=-1).validate()
        with self.assertRaises(ValueError):
            AppSettings(manual_analysis_interval_seconds=3601).validate()

    def test_skipped_update_version_must_be_text(self):
        with self.assertRaises(ValueError):
            AppSettings(skipped_update_version=123).validate()

    def test_watch_folders_round_trip_and_legacy_input_remains_supported(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            first = str(Path(temp) / "downloads")
            second = str(Path(temp) / "scanner")
            settings = AppSettings(
                input_dir=first,
                watch_folders=[first, second],
            )
            save_settings(settings, path)
            self.assertEqual(load_settings(path).watch_folders, [first, second])

    def test_recursive_watch_setting_round_trips(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "settings.json"
            settings = AppSettings(watch_subdirectories=True)

            save_settings(settings, path)

            self.assertTrue(load_settings(path).watch_subdirectories)

    def test_recursive_watch_rejects_library_inside_watch_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = AppSettings(
                watch_folders=[str(root)],
                watch_subdirectories=True,
                library_root=str(root / "library"),
            )

            with self.assertRaises(ValueError):
                settings.validate()

    def test_duplicate_watch_folders_are_rejected(self):
        settings = AppSettings(watch_folders=["C:/papers", "c:/PAPERS"])
        with self.assertRaises(ValueError):
            settings.validate()

    def test_research_categories_are_editable_unique_names(self):
        AppSettings(
            research_categories=["생명과학", "사용자 정의 분야"],
            focus_categories=["사용자 정의 분야"],
        ).validate()
        with self.assertRaises(ValueError):
            AppSettings(
                research_categories=["생명과학", "생명과학"],
            ).validate()
        with self.assertRaises(ValueError):
            AppSettings(
                research_categories=["생명과학"],
                focus_categories=["삭제된 분야"],
            ).validate()


if __name__ == "__main__":
    unittest.main()
