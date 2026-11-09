"""Smoke-test packaged PDF raster and native scene workers without opening a GUI."""

import json
from pathlib import Path
import subprocess
import tempfile

import pymupdf


def main():
    bundle = Path("dist/PaperOrganizer").resolve()
    native = bundle / "_internal/native"
    for path in (bundle / "PaperOrganizer.exe", bundle / "ocr/leaflet-ocr.exe",
                 native / "leaflet_mupdf_path.dll", native / "leaflet-gpu-scene-worker.exe",
                 bundle / "_internal/pdfeditor/_path_packer.pyd"):
        if not path.is_file():
            raise FileNotFoundError(path)
    with tempfile.TemporaryDirectory() as temp:
        pdf = Path(temp) / "sample.pdf"
        with pymupdf.open() as document:
            page = document.new_page(width=100, height=100)
            page.insert_text((10, 30), "Reader smoke")
            document.save(pdf)
        job = (json.dumps({"path": str(pdf), "target": 100}) + "\n"
               + json.dumps({"page": 0}) + "\n").encode()
        process = subprocess.run([str(bundle / "PaperOrganizer.exe"), "--ocr-render-worker"],
                                 input=job, capture_output=True, timeout=60, check=True)
        header, pixels = process.stdout.split(b"\n", 1)
        result = json.loads(header)
        if result.get("size") != len(pixels) or not pixels:
            raise RuntimeError("Packaged PDF raster worker returned invalid pixels")
        sdk = bundle / "_internal/pymupdf/mupdfcpp64.dll"
        output = Path(temp) / "scene.bin"
        subprocess.run([str(native / "leaflet-gpu-scene-worker.exe"), str(pdf), str(output),
                        "--bridge", str(native / "leaflet_mupdf_path.dll"),
                        "--mupdf", str(sdk), "--expected-mupdf", pymupdf.VersionFitz,
                        "--timeout", "30"], capture_output=True, timeout=60, check=True)
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError("Packaged GPU scene worker returned an empty scene")
    print("Packaged reader workers: OK")


if __name__ == "__main__":
    main()
