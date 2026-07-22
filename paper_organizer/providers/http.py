"""Small standard-library JSON HTTP transport used by all providers."""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from paper_organizer.infra.redaction import SENSITIVE_HEADER_NAMES

from .base import ProviderError


ALLOWED_CREDENTIAL_HOSTS = frozenset({"api.openai.com", "api.anthropic.com"})


def _validate_credential_destination(url: str, headers: Mapping[str, str]) -> None:
    has_credentials = any(
        name.lower() in SENSITIVE_HEADER_NAMES for name in headers
    )
    if not has_credentials:
        return
    destination = urlsplit(url)
    if (
        destination.scheme.lower() != "https"
        or destination.hostname not in ALLOWED_CREDENTIAL_HOSTS
    ):
        raise ProviderError("Refusing to send API credentials to an untrusted host")


class _CredentialSafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        request_headers = dict(req.header_items())
        if any(
            name.lower() in SENSITIVE_HEADER_NAMES for name in request_headers
        ):
            raise ProviderError("Refusing to redirect a request containing API credentials")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibJsonHttpClient:
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        _validate_credential_destination(url, headers)
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            opener = build_opener(_CredentialSafeRedirectHandler())
            with opener.open(request, timeout=timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ProviderError(
                f"Provider HTTP request failed with status {exc.code}"
            ) from None
        except URLError:
            raise ProviderError("Provider could not be reached") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProviderError("Provider returned an invalid JSON response") from None
        if not isinstance(raw, dict):
            raise ProviderError("Provider response must be a JSON object")
        return raw
