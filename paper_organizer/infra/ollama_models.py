"""Safe local-only Ollama model download, verification and deletion client."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from threading import Event
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from paper_organizer.infra.ollama_runtime import (
    InstalledOllamaModel,
    parse_installed_models,
    parse_running_models,
)


_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,254}$")
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class OllamaModelError(RuntimeError):
    """Raised when a local Ollama model operation fails safely."""


class OllamaOperationCancelled(OllamaModelError):
    """Raised after a user explicitly cancels a streaming model pull."""


@dataclass(frozen=True, slots=True)
class OllamaPullProgress:
    status: str
    completed_bytes: int
    total_bytes: int
    digest: str = ""

    @property
    def percent(self) -> int | None:
        if self.total_bytes <= 0:
            return None
        return max(0, min(100, round(self.completed_bytes * 100 / self.total_bytes)))


@dataclass(frozen=True, slots=True)
class OllamaVerification:
    model: InstalledOllamaModel
    response_valid: bool
    message: str
    processor: str = ""
    prompt_tokens_per_second: float = 0.0
    output_tokens_per_second: float = 0.0
    total_seconds: float = 0.0


ProgressCallback = Callable[[OllamaPullProgress], None]
UrlOpener = Callable[..., Any]


class OllamaModelClient:
    """Perform mutating Ollama calls only against a loopback HTTP endpoint."""

    def __init__(
        self,
        opener: UrlOpener | None = None,
        endpoint: str = "http://127.0.0.1:11434",
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.hostname not in _LOCAL_HOSTS:
            raise ValueError("Ollama model management is restricted to local HTTP")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Ollama endpoint must be a plain local HTTP origin")
        self._open = opener or urlopen
        self._endpoint = endpoint.rstrip("/")

    def pull(
        self,
        model: str,
        *,
        on_progress: ProgressCallback | None = None,
        cancel: Event | None = None,
        timeout: float = 30.0,
    ) -> OllamaPullProgress:
        model_id = validate_model_name(model)
        request = self._request(
            "/api/pull",
            {"model": model_id, "stream": True, "insecure": False},
        )
        last = OllamaPullProgress("다운로드 준비", 0, 0)
        succeeded = False
        try:
            with self._open(request, timeout=timeout) as response:
                self._ensure_success(response)
                while True:
                    if cancel is not None and cancel.is_set():
                        raise OllamaOperationCancelled("모델 다운로드가 취소되었습니다.")
                    raw_line = response.readline()
                    if not raw_line:
                        break
                    payload = _decode_object(raw_line)
                    if payload.get("error"):
                        raise OllamaModelError(str(payload["error"]))
                    last = OllamaPullProgress(
                        status=str(payload.get("status") or "처리 중"),
                        completed_bytes=_non_negative_int(payload.get("completed")),
                        total_bytes=_non_negative_int(payload.get("total")),
                        digest=str(payload.get("digest") or ""),
                    )
                    if on_progress is not None:
                        on_progress(last)
                    if last.status.casefold() == "success":
                        succeeded = True
                        break
        except OllamaOperationCancelled:
            raise
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise OllamaModelError(_error_message(exc)) from exc
        if not succeeded:
            raise OllamaModelError(
                f"Ollama가 다운로드 완료를 확인하지 않았습니다: {last.status}"
            )
        return last

    def verify(self, model: str, *, timeout: float = 120.0) -> OllamaVerification:
        model_id = validate_model_name(model)
        tags = self._request_json("/api/tags", None, method="GET", timeout=5.0)
        installed = parse_installed_models(tags)
        matched = next(
            (item for item in installed if _same_model(item.name, model_id)),
            None,
        )
        if matched is None:
            raise OllamaModelError("다운로드 후 설치 모델 목록에서 찾을 수 없습니다.")
        schema = {
            "type": "object",
            "properties": {"ready": {"type": "boolean"}},
            "required": ["ready"],
            "additionalProperties": False,
        }
        generated = self._request_json(
            "/api/generate",
            {
                "model": model_id,
                "prompt": 'Return only JSON: {"ready": true}',
                "format": schema,
                "stream": False,
                "think": False,
                "keep_alive": "1m",
                "options": {"temperature": 0, "num_predict": 24},
            },
            method="POST",
            timeout=timeout,
        )
        try:
            response_text = str(generated.get("response") or "").strip()
            try:
                decoded = json.loads(response_text)
            except json.JSONDecodeError as exc:
                raise OllamaModelError(
                    "설치 모델의 JSON 응답 검증에 실패했습니다."
                ) from exc
            if not isinstance(decoded, Mapping) or decoded.get("ready") is not True:
                raise OllamaModelError(
                    "설치 모델이 예상한 검증 응답을 반환하지 않았습니다."
                )
            processor = self._running_processor(model_id)
            prompt_tps = _tokens_per_second(
                generated.get("prompt_eval_count"),
                generated.get("prompt_eval_duration"),
            )
            output_tps = _tokens_per_second(
                generated.get("eval_count"),
                generated.get("eval_duration"),
            )
            total_seconds = round(
                _non_negative_int(generated.get("total_duration")) / 1_000_000_000,
                3,
            )
            details = [processor] if processor else []
            if output_tps:
                details.append(f"출력 {output_tps:g} tok/s")
            message = "설치 및 짧은 JSON 응답 검증 완료"
            if details:
                message += " · " + " · ".join(details)
            return OllamaVerification(
                matched,
                True,
                message,
                processor=processor,
                prompt_tokens_per_second=prompt_tps,
                output_tokens_per_second=output_tps,
                total_seconds=total_seconds,
            )
        finally:
            self._unload_after_verification(model_id)

    def _running_processor(self, model: str) -> str:
        try:
            running = parse_running_models(
                self._request_json("/api/ps", None, method="GET", timeout=5.0)
            )
        except (OllamaModelError, RuntimeError, ValueError):
            return ""
        matched = next(
            (item for item in running if _same_model(item.name, model)),
            None,
        )
        return matched.processor if matched is not None else ""

    def _unload_after_verification(self, model: str) -> None:
        try:
            self._request_json(
                "/api/generate",
                {
                    "model": model,
                    "prompt": "",
                    "stream": False,
                    "keep_alive": 0,
                    "options": {"num_predict": 1},
                },
                method="POST",
                timeout=30.0,
            )
        except OllamaModelError:
            pass

    def delete(self, model: str, *, timeout: float = 30.0) -> None:
        model_id = validate_model_name(model)
        self._request_json(
            "/api/delete",
            {"model": model_id},
            method="DELETE",
            timeout=timeout,
            allow_empty=True,
        )
        tags = self._request_json("/api/tags", None, method="GET", timeout=5.0)
        if any(_same_model(item.name, model_id) for item in parse_installed_models(tags)):
            raise OllamaModelError("삭제 요청 후에도 모델이 설치 목록에 남아 있습니다.")

    def _request_json(
        self,
        path: str,
        payload: Mapping[str, Any] | None,
        *,
        method: str,
        timeout: float,
        allow_empty: bool = False,
    ) -> Mapping[str, Any]:
        request = self._request(path, payload, method=method)
        try:
            with self._open(request, timeout=timeout) as response:
                self._ensure_success(response)
                raw = response.read()
        except (HTTPError, URLError, OSError) as exc:
            raise OllamaModelError(_error_message(exc)) from exc
        if not raw and allow_empty:
            return {}
        try:
            return _decode_object(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise OllamaModelError("Ollama 응답이 올바른 JSON 객체가 아닙니다.") from exc

    def _request(
        self,
        path: str,
        payload: Mapping[str, Any] | None,
        method: str = "POST",
    ) -> Request:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return Request(
            f"{self._endpoint}{path}",
            data=data,
            headers=headers,
            method=method,
        )

    @staticmethod
    def _ensure_success(response: Any) -> None:
        status = int(getattr(response, "status", 200))
        if not 200 <= status < 300:
            raise OllamaModelError(f"Ollama HTTP {status}")


def validate_model_name(model: str) -> str:
    value = model.strip()
    if not _MODEL_NAME.fullmatch(value) or ".." in value or "//" in value:
        raise ValueError("올바른 Ollama 모델 이름이 아닙니다.")
    return value


def _decode_object(raw: bytes | str) -> Mapping[str, Any]:
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    decoded = json.loads(text)
    if not isinstance(decoded, Mapping):
        raise ValueError("JSON object required")
    return decoded


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _tokens_per_second(count: Any, duration: Any) -> float:
    tokens = _non_negative_int(count)
    nanoseconds = _non_negative_int(duration)
    if not tokens or not nanoseconds:
        return 0.0
    return round(tokens / (nanoseconds / 1_000_000_000), 2)


def _same_model(left: str, right: str) -> bool:
    def aliases(value: str) -> set[str]:
        key = value.strip().casefold()
        values = {key}
        if key.endswith(":latest"):
            values.add(key.removesuffix(":latest"))
        else:
            values.add(f"{key}:latest")
        return values

    return bool(aliases(left) & aliases(right))


def _error_message(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        try:
            payload = _decode_object(exc.read())
            if payload.get("error"):
                return str(payload["error"])
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return f"Ollama HTTP {exc.code}"
    return str(exc) or exc.__class__.__name__
