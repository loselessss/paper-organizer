# -*- mode: python ; coding: utf-8 -*-
"""One-folder Paper Organizer GUI plus an isolated sPDF OCR worker."""

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules


sys.path.insert(0, os.path.abspath("vendor/spdf"))
sys.path.insert(0, os.path.abspath("."))

from paper_organizer.infra.llama_bundle import BUNDLE_DIR, VULKAN_DIR, validate_bundle

validate_bundle(BUNDLE_DIR)
validate_bundle(VULKAN_DIR, backend="vulkan")


ocr_datas, ocr_bins, ocr_hidden = [], [], []
for package in ("rapidocr", "onnxruntime"):
    datas, binaries, hidden = collect_all(package)
    ocr_datas += datas
    ocr_bins += binaries
    ocr_hidden += hidden
ocr_hidden += ["cv2", "numpy", "fitz", "pdfeditor.ocr_subprocess"]

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
    excludes=["PyQt5", "tkinter", "matplotlib", "onnx", "tensorrt", "paddle"],
)
pyz_ocr = PYZ(a_ocr.pure)
exe_ocr = EXE(
    pyz_ocr,
    a_ocr.scripts,
    [],
    exclude_binaries=True,
    name="spdf-ocr",
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
    ("vendor/spdf/LICENSES.md", "."),
    *llm_datas,
]
a_gui = Analysis(
    ["run_gui.py"],
    pathex=[".", "vendor/spdf"],
    binaries=[],
    datas=main_datas,
    hiddenimports=[
        "fitz",
        "keyring.backends.Windows",
        *spdf_hidden,
    ],
    excludes=[
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
