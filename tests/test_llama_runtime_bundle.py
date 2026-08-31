"""Verify pinned runtime packaging without network or native processes."""

import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import prepare_llama_runtime as bundle


class LlamaRuntimeBundleTests(unittest.TestCase):
    def archive(self, root, *, omit=(), extra=None):
        archive = root / "runtime.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for name in bundle.REQUIRED - {bundle.LICENSE.name} - set(omit):
                package.writestr(name, b"fake runtime")
            package.writestr("ggml-cpu-test.dll", b"CPU backend")
            package.writestr("llama-cli.exe", b"not needed")
            if extra:
                package.writestr(extra, b"unsafe")
        return archive

    def test_includes_every_dll_and_licenses_but_not_unneeded_tools(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self.archive(root)
            with patch.object(bundle, "SHA256", bundle.digest(archive)):
                output = bundle.prepare_bundle(archive, root / "output")
                bundle.validate_bundle(output)
                self.assertTrue((output / "ggml-cpu-test.dll").is_file())
                self.assertTrue((output / bundle.LICENSE.name).is_file())
                self.assertFalse((output / "llama-cli.exe").exists())
                self.assertEqual(bundle.prepare_bundle(archive, output), output)

    def test_archive_hash_failure_does_not_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                bundle.prepare_bundle(self.archive(root), root / "output")
            self.assertFalse((root / "output").exists())

    def test_missing_server_or_dll_or_license_does_not_publish(self):
        for missing in ("llama-server.exe", "llama-server-impl.dll", "LICENSE-LLVM-OpenMP"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = self.archive(root, omit=(missing,))
                with patch.object(bundle, "SHA256", bundle.digest(archive)):
                    with self.assertRaises(ValueError):
                        bundle.prepare_bundle(archive, root / "output")
                self.assertFalse((root / "output").exists())

    def test_rejects_unsafe_archive_paths(self):
        for name in ("../escape.dll", "C:/escape.dll", "folder\\escape.dll"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = self.archive(root, extra=name)
                with patch.object(bundle, "SHA256", bundle.digest(archive)):
                    with self.assertRaisesRegex(ValueError, "경로"):
                        bundle.prepare_bundle(archive, root / "output")
                self.assertFalse((root / "output").exists())

    def test_altered_or_missing_or_extra_files_fail_validation(self):
        for kind in ("changed", "missing", "extra"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = self.archive(root)
                with patch.object(bundle, "SHA256", bundle.digest(archive)):
                    output = bundle.prepare_bundle(archive, root / "output")
                    if kind == "changed":
                        (output / "llama.dll").write_bytes(b"changed")
                    elif kind == "missing":
                        (output / "llama.dll").unlink()
                    else:
                        (output / "unexpected.dll").write_bytes(b"extra")
                    with self.assertRaises(ValueError):
                        bundle.validate_bundle(output)

    def test_stale_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = self.archive(root)
            with patch.object(bundle, "SHA256", bundle.digest(archive)):
                output = bundle.prepare_bundle(archive, root / "output")
                manifest_path = output / "runtime-manifest.json"
                manifest = json.loads(manifest_path.read_text())
                manifest["version"] = "old"
                manifest_path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "버전"):
                    bundle.validate_bundle(output)

    def test_bad_download_preserves_existing_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "runtime.zip"
            archive.write_bytes(b"previous")
            with patch.object(bundle, "urlopen", return_value=io.BytesIO(b"bad download")):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    bundle.download_archive(archive)
            self.assertEqual(archive.read_bytes(), b"previous")

    def test_smoke_uses_isolated_path_and_reports_native_failure(self):
        with patch.object(bundle.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "server")) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                bundle.smoke_check(Path("runtime"))
            self.assertTrue(run.call_args.kwargs["check"])
            self.assertEqual(run.call_args.args[0][-1], "--version")
            self.assertNotIn(".venv", run.call_args.kwargs["env"]["PATH"])

    def test_build_prepares_and_verifies_complete_bundle(self):
        build = (bundle.ROOT / "build_exe.bat").read_text()
        spec = (bundle.ROOT / "paper-organizer.spec").read_text()
        runtime = (bundle.ROOT / "paper_organizer/infra/embedded_llm_runtime.py").read_text(encoding="utf-8")
        self.assertIn("prepare_llama_runtime.py --smoke", build)
        self.assertIn('prepare_llama_runtime.py --verify "dist\\PaperOrganizer\\_internal\\llm" --smoke', build)
        self.assertIn("validate_bundle(BUNDLE_DIR)", spec)
        self.assertIn('(str(BUNDLE_DIR), "llm")', spec)
        self.assertIn(f'"{bundle.VERSION}"', runtime)


if __name__ == "__main__":
    unittest.main()
