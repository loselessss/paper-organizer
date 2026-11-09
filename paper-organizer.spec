# -*- mode: python ; coding: utf-8 -*-
"""One-folder Paper Organizer GUI plus an isolated sPDF OCR worker."""

import os
import sys

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, collect_data_files


sys.path.insert(0, os.path.abspath("vendor/spdf"))
sys.path.insert(0, os.path.abspath("."))

from paper_organizer.infra.llama_bundle import BUNDLE_DIR, VULKAN_DIR, validate_bundle

validate_bundle(BUNDLE_DIR)
validate_bundle(VULKAN_DIR, backend="vulkan")

from tools.build.build_payload import native_worker_runtime_binaries, prefer_ocr_runtimes, runtime_ssl_datas
import pymupdf
from PyQt5.QtCore import QLibraryInfo

native = Path("vendor/spdf/native/bin")
scene_worker = native / "leaflet-gpu-scene-worker.exe"
mupdf_bridge = native / "leaflet_mupdf_path.dll"
for required in (scene_worker, mupdf_bridge, Path("vendor/spdf/pdfeditor/_path_packer.pyd")):
    if not required.is_file():
        raise FileNotFoundError(f"Build the Leaflet native modules first: {required}")
worker_runtimes = native_worker_runtime_binaries(
    scene_worker, mupdf_bridge, Path(pymupdf.__file__).parent / "mupdfcpp64.dll",
    search_paths=[str(Path(pymupdf.__file__).parent), sys.base_prefix,
                  QLibraryInfo.location(QLibraryInfo.BinariesPath)],
)


ocr_datas, ocr_bins, ocr_hidden = [], [], []
for package in ("rapidocr", "onnxruntime"):
    datas, binaries, hidden = collect_all(package)
    ocr_datas += datas
    ocr_bins += binaries
    ocr_hidden += hidden
ocr_hidden += ["cv2", "numpy", "pdfeditor.ocr_subprocess"]

_OCR_MODELS = (
    "PP-OCRv6_det_small.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "korean_PP-OCRv5_rec_mobile.onnx",
)


def keep_ocr_entry(entry):
    source = entry[0].replace("\\", "/")
    if "/rapidocr/models/" in source and source.endswith(".onnx"):
        return any(source.endswith(name) for name in _OCR_MODELS)
    return True


ocr_datas = [entry for entry in ocr_datas if keep_ocr_entry(entry)]
ocr_bins = [entry for entry in ocr_bins if keep_ocr_entry(entry)]

a_ocr = Analysis(
    ["paper_organizer/ocr_worker_main.py"],
    pathex=["vendor/spdf"],
    binaries=ocr_bins,
    datas=ocr_datas,
    hiddenimports=ocr_hidden,
    excludes=["PyQt5", "pymupdf", "fitz", "tkinter", "matplotlib", "onnx", "tensorrt", "paddle", "PIL.AvifImagePlugin"],
)
a_ocr.binaries = prefer_ocr_runtimes(a_ocr.binaries)
pyz_ocr = PYZ(a_ocr.pure)
exe_ocr = EXE(
    pyz_ocr,
    a_ocr.scripts,
    [],
    exclude_binaries=True,
    name="leaflet-ocr",
    console=True,
)
coll_ocr = COLLECT(
    exe_ocr,
    a_ocr.binaries,
    a_ocr.datas,
    name="PaperOrganizer-ocr",
)

spdf_hidden = collect_submodules("pdfeditor")
# Keep llama-server.exe, all companion DLLs and licenses in one directory.
llm_datas = [
    (str(BUNDLE_DIR), "llm/cpu"),
    (str(VULKAN_DIR), "llm/vulkan"),
]
main_datas = [
    ("paper_organizer/assets", "paper_organizer/assets"),
    ("paper_organizer/models", "paper_organizer/models"),
    ("vendor/spdf/pdfeditor", "vendor/spdf/pdfeditor"),
    ("vendor/spdf/assets/spdf.ico", "assets"),
    ("vendor/spdf/assets/spdf_doc.ico", "assets"),
    ("vendor/spdf/assets/leaflet_ai.ico", "assets"),
    ("vendor/spdf/assets/leaflet_eps.ico", "assets"),
    ("vendor/spdf/LICENSES.md", "."),
    ("SOURCE_CODE.md", "."),
    ("vendor/spdf/licenses", "licenses"),
    ("vendor/spdf/native/bin/path-packer-legal", "third-party/leaflet-path-packer"),
    *llm_datas,
    *runtime_ssl_datas(),
    *collect_data_files("pymupdf", include_py_files=True, includes=["layout/**"]),
]
a_gui = Analysis(
    ["run_gui.py"],
    pathex=[".", "vendor/spdf"],
    runtime_hooks=["vendor/spdf/tools/build/rth_headless_workers.py"],
    binaries=[("vendor/spdf/native/bin/spdf_d2d_renderer.dll", "native"),
              (str(scene_worker), "native"), (str(mupdf_bridge), "native"),
              ("vendor/spdf/pdfeditor/_path_packer.pyd", "pdfeditor"), *worker_runtimes],
    datas=main_datas,
    hiddenimports=[
        "fitz",
        "keyring.backends.Windows",
        "pymupdf.features",
        "packaging.version",
        "yaml",
        *spdf_hidden,
    ],
    excludes=[
        "pymupdf.layout",
        "PIL.AvifImagePlugin",
        "rapidocr",
        "onnxruntime",
        "cv2",
        "onnx",
        "tensorrt",
        "paddle",
        "tkinter",
        "matplotlib",
    ],
)
a_gui.binaries = prefer_ocr_runtimes(a_gui.binaries)
pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="PaperOrganizer",
    console=False,
    icon="paper_organizer/assets/paper-organizer.ico",
)
coll_gui = COLLECT(
    exe_gui,
    a_gui.binaries,
    a_gui.datas,
    name="PaperOrganizer",
)
