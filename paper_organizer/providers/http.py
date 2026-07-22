"""Small standard-library JSON HTTP transport used by all providers."""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .base import ProviderError


class UrllibJsonHttpClient:
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ProviderError(f"Provider HTTP request failed with status {exc.code}") from exc
        except URLError as exc:
            raise ProviderError("Provider could not be reached") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("Provider returned an invalid JSON response") from exc
        if not isinstance(raw, dict):
            raise ProviderError("Provider response must be a JSON object")
        return raw
