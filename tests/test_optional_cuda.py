"""Check optional CUDA downloads and GPU fallback without network or hardware."""

import hashlib
import io
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from paper_organizer.application.cuda_runtime_manager import CudaRuntimeManager
from paper_organizer.infra import embedded_llm_runtime as runtime, llama_bundle as bundle
from paper_organizer.infra.settings import AppSettings


class OptionalCudaTests(unittest.TestCase):
    def test_explicit_download_verifies_both_archives_and_installs_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            payloads = {}
            for backend, names in (
                ("cuda", (bundle.REQUIRED - {bundle.LICENSE.name}) | {"ggml-cuda.dll"}),
                ("cudart", {"cudart64_12.dll", "cublas64_12.dll", "cublasLt64_12.dll"}),
            ):
                stream = io.BytesIO()
                with zipfile.ZipFile(stream, "w") as archive:
                    for name in names:
                        archive.writestr(name, b"fake DLL")
                payloads[bundle.asset(backend)[1]] = stream.getvalue()
            opener = Mock(side_effect=lambda url, **kw: io.BytesIO(payloads[url]))
            manager = CudaRuntimeManager(root / "cuda", opener=opener)
            self.assertFalse(manager.installed())
            opener.assert_not_called()
            with patch.object(bundle, "CUDA_SHA256", hashlib.sha256(payloads[bundle.CUDA_URL]).hexdigest()), patch.object(bundle, "CUDART_SHA256", hashlib.sha256(payloads[bundle.CUDART_URL]).hexdigest()):
                manager.install()
                bundle.validate_bundle(manager.directory, backend="cuda")
                self.assertTrue(manager.installed())
                self.assertTrue((manager.directory / bundle.CUDA_NOTICE.name).is_file())
                manager.install()
                self.assertEqual(opener.call_count, 2)

    def test_cancel_does_not_contact_server(self):
        with tempfile.TemporaryDirectory() as temp:
            opener = Mock()
            manager = CudaRuntimeManager(Path(temp) / "cuda", opener=opener)
            cancel = Event()
            cancel.set()
            with self.assertRaisesRegex(RuntimeError, "취소"):
                manager.install(cancel=cancel)
            opener.assert_not_called()
            self.assertFalse(manager.directory.exists())

    def test_invalid_download_does_not_install_or_leave_partial_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manager = CudaRuntimeManager(root / "cuda", opener=Mock(return_value=io.BytesIO(b"bad")))
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                manager.install()
            self.assertFalse(manager.directory.exists())
            self.assertEqual(list((root / "downloads").iterdir()), [])

    def test_backend_order_and_explicit_device_and_loopback(self):
        with patch.object(runtime, "bundled_server", side_effect=lambda backend: Path(backend) / "llama-server.exe"), patch.object(runtime, "_devices", side_effect=lambda exe, backend, allow: [backend.upper() + "0"]):
            commands = runtime._runtime_commands(AppSettings(selected_model="test"))
        self.assertEqual([backend for backend, _ in commands], ["cuda", "vulkan", "cpu"])
        for backend, command in commands:
            self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
            self.assertEqual(command[command.index("--gpu-layers") + 1], "0" if backend == "cpu" else "auto")
        self.assertEqual(commands[-1][1][-3], "none")

    def test_no_gpu_uses_cpu_only(self):
        with patch.object(runtime, "bundled_server", return_value=Path("server")), patch.object(runtime, "_devices", return_value=[]):
            self.assertEqual([b for b, _ in runtime._runtime_commands(AppSettings())], ["cpu"])

    def test_integrated_gpu_opt_out_and_discrete_preference(self):
        output = "  Vulkan0: Integrated (8000 MiB, 7000 MiB free)\n  Vulkan1: Discrete (12000 MiB, 10000 MiB free)\n"
        with patch.object(runtime.subprocess, "run", return_value=SimpleNamespace(stdout=output)), patch("paper_organizer.infra.vulkan_devices.device_types", return_value={"Integrated": 1, "Discrete": 2}):
            self.assertEqual(runtime._devices(Path("server"), "vulkan", False), ["Vulkan1"])
            self.assertEqual(runtime._devices(Path("server"), "vulkan", True), ["Vulkan1", "Vulkan0"])

    def test_failed_gpu_process_falls_back_to_cpu(self):
        dead = Mock()
        dead.poll.return_value = 1
        live = Mock()
        live.poll.return_value = None
        with patch.object(runtime, "_MANAGED_PROCESS", dead), patch.object(runtime, "_MANAGED_CONFIG", ("test", True)), patch.object(runtime, "_MANAGED_BACKEND", "cuda"), patch.object(runtime, "_PENDING_COMMANDS", [("vulkan", ["vulkan"]), ("cpu", ["cpu"])]), patch.object(runtime.subprocess, "Popen", side_effect=[OSError("no Vulkan"), live]), patch.object(runtime, "_healthy", return_value=True):
            self.assertTrue(runtime.wait_until_ready(1))
            self.assertEqual(runtime.runtime_backend(), "cpu")

    def test_bibliography_mode_does_not_start_runtime(self):
        with patch.object(runtime, "stop_runtime") as stop, patch.object(runtime, "_runtime_commands") as commands:
            self.assertFalse(runtime.start_runtime(AppSettings(bibliography_only=True)))
            stop.assert_called_once()
            commands.assert_not_called()


if __name__ == "__main__":
    unittest.main()
