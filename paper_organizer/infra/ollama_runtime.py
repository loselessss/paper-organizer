"""Read-only inspection of the local Ollama runtime and installed models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class InstalledOllamaModel:
    name: str
    size_gb: float
    parameter_size: str
    quantization: str
    modified_at: str


@dataclass(frozen=True, slots=True)
class OllamaRuntimeStatus:
    reachable: bool
    version: str
    models: tuple[InstalledOllamaModel, ...]
    error: str = ""


JsonFetcher = Callable[[str, float], Mapping[str, Any]]


def _fetch_json(url: str, timeout: float) -> Mapping[str, Any]:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        decoded = json.loads(response.read().decode("utf-8"))
    if not isinstance(decoded, Mapping):
        raise RuntimeError("Ollama response is not a JSON object")
    return decoded


class OllamaRuntimeInspector:
    def __init__(
        self,
        fetch_json: JsonFetcher | None = None,
        endpoint: str = "http://127.0.0.1:11434",
    ) -> None:
        self._fetch = fetch_json or _fetch_json
        self._endpoint = endpoint.rstrip("/")

    def inspect(self, timeout: float = 1.5) -> OllamaRuntimeStatus:
        try:
            version_data = self._fetch(f"{self._endpoint}/api/version", timeout)
            tags_data = self._fetch(f"{self._endpoint}/api/tags", timeout)
            raw_models = tags_data.get("models", [])
            if not isinstance(raw_models, list):
                raise RuntimeError("Ollama model list is invalid")
            models: list[InstalledOllamaModel] = []
            for raw in raw_models:
                if not isinstance(raw, Mapping):
                    continue
                details = raw.get("details")
                details = details if isinstance(details, Mapping) else {}
                name = str(raw.get("name") or raw.get("model") or "").strip()
                if not name:
                    continue
                size = raw.get("size", 0)
                size_gb = (
                    round(float(size) / (1000**3), 2)
                    if isinstance(size, (int, float)) and not isinstance(size, bool)
                    else 0.0
                )
                models.append(
                    InstalledOllamaModel(
                        name=name,
                        size_gb=size_gb,
                        parameter_size=str(details.get("parameter_size") or ""),
                        quantization=str(details.get("quantization_level") or ""),
                        modified_at=str(raw.get("modified_at") or ""),
                    )
                )
            return OllamaRuntimeStatus(
                reachable=True,
                version=str(version_data.get("version") or "unknown"),
                models=tuple(sorted(models, key=lambda item: item.name.casefold())),
            )
        except (OSError, ValueError, RuntimeError, HTTPError, URLError) as exc:
            return OllamaRuntimeStatus(False, "", (), str(exc))
