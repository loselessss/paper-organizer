"""Explicitly audit model URLs using headers and four bytes, never full weights."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "paper_organizer/models/catalog.json"


def check_model(model: dict, *, opener=urlopen) -> dict:
    result = {"id": model["id"], "url": model.get("download_url", ""), "ok": False}
    if not result["url"]:
        return {**result, "error": "다운로드 주소 없음"}
    try:
        with opener(Request(result["url"], method="HEAD"), timeout=20) as response:
            result["status"] = response.status
            result["size_bytes"] = int(response.headers.get("Content-Length", 0))
        # Closing after four bytes also bounds reads if the server ignores Range.
        request = Request(result["url"], headers={"Range": "bytes=0-3"})
        with opener(request, timeout=20) as response:
            if response.status not in {200, 206} or response.read(4) != b"GGUF":
                return {**result, "error": "GGUF 파일이 아닌 응답"}
        result["ok"] = True
    except HTTPError as exc:
        result.update(status=exc.code, error=f"HTTP {exc.code}")
    except (URLError, OSError, ValueError) as exc:
        result["error"] = type(exc).__name__
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    args = parser.parse_args()
    models = json.loads(args.catalog.read_text(encoding="utf-8"))["models"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(check_model, models))
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
