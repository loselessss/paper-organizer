"""Prepare the pinned CPU/Vulkan bundle and optional CUDA download."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paper_organizer.infra.llama_bundle import main


if __name__ == "__main__":
    raise SystemExit(main())
