"""Manage app-owned GGUF model files without Ollama."""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.error import HTTPError
from urllib.request import urlopen

from paper_organizer.core.model_recommendation import (
    ModelSpec,
    load_model_catalog,
    model_usage_guidance,
)
from paper_organizer.infra.embedded_llm_runtime import (
    default_model_dir,
    model_path_for_id,
)
from paper_organizer.infra.hardware import HardwareInspector
from paper_organizer.infra.settings import (
    default_settings_path,
    load_settings,
    save_settings,
)


@dataclass(frozen=True, slots=True)
class EmbeddedModelEntry:
    model_id: str
    label: str
    path: Path
    installed: bool
    installed_size_gb: float
    estimated_download_gb: float
    download_available: bool
    parameter_size: str
    usage_guidance: str


@dataclass(frozen=True, slots=True)
class EmbeddedModelSnapshot:
    disk_path: Path
    disk_free_gb: float
    entries: tuple[EmbeddedModelEntry, ...]


@dataclass(frozen=True, slots=True)
class EmbeddedDownloadPlan:
    model_id: str
    label: str
    url: str
    target: Path
    estimated_download_gb: float
    required_free_gb: float
    available_free_gb: float
    already_installed: bool
    can_download: bool
    reason: str


@dataclass(frozen=True, slots=True)
class EmbeddedDownloadProgress:
    phase: str
    received_bytes: int = 0
    total_bytes: int | None = None


class EmbeddedModelManagerService:
    def __init__(
        self,
        settings_path: Path | None = None,
        *,
        catalog_path: Path | None = None,
        hardware: HardwareInspector | None = None,
        model_dir: Path | None = None,
        opener: Callable[[str], object] | None = None,
    ) -> None:
        self._settings_path = settings_path or default_settings_path()
        self._catalog_path = catalog_path
        self._hardware = hardware or HardwareInspector()
        self._model_dir = model_dir or default_model_dir()
        self._opener = opener or (lambda url: urlopen(url, timeout=30))

    def snapshot(self) -> EmbeddedModelSnapshot:
        hardware = self._hardware.inspect()
        _version, specs = load_model_catalog(self._catalog_path)
        entries = tuple(
            _entry_from_spec(spec, self._model_path(spec.model_id))
            for spec in specs
        )
        return EmbeddedModelSnapshot(
            disk_path=self._model_dir,
            disk_free_gb=hardware.model_disk_free_gb,
            entries=entries,
        )

    def plan_download(self, model: str) -> EmbeddedDownloadPlan:
        spec = _find_spec(model, self._catalog_path)
        target = self._model_path(spec.model_id)
        snapshot = self.snapshot()
        entry = next(item for item in snapshot.entries if item.model_id == spec.model_id)
        required = round(spec.download_gb * 1.5 + 2.0, 2)
        enough = snapshot.disk_free_gb >= required
        if entry.installed:
            reason = "이미 설치되어 있어 다시 다운로드할 필요가 없습니다."
        elif not spec.download_url:
            reason = "이 모델은 아직 직접 다운로드 주소가 등록되지 않았습니다."
        elif not enough:
            reason = f"안전한 다운로드에는 최소 {required:g}GB의 여유 공간이 필요합니다."
        else:
            reason = "다운로드와 임시 파일을 위한 안전 여유가 충분합니다."
        return EmbeddedDownloadPlan(
            model_id=spec.model_id,
            label=spec.label,
            url=spec.download_url,
            target=target,
            estimated_download_gb=spec.download_gb,
            required_free_gb=required,
            available_free_gb=snapshot.disk_free_gb,
            already_installed=entry.installed,
            can_download=bool(spec.download_url) and not entry.installed and enough,
            reason=reason,
        )

    def download(
        self,
        model: str,
        *,
        on_progress: Callable[[EmbeddedDownloadProgress], None] | None = None,
        cancel: Event | None = None,
    ) -> Path:
        plan = self.plan_download(model)
        if not plan.can_download:
            raise ValueError(plan.reason)
        plan.target.parent.mkdir(parents=True, exist_ok=True)
        temp_path = plan.target.with_suffix(plan.target.suffix + ".part")
        digest = hashlib.sha256()
        received = 0
        if on_progress is not None:
            on_progress(EmbeddedDownloadProgress("downloading"))
        try:
            with self._opener(plan.url) as response:
                total = _content_length(response)
                with open(temp_path, "wb") as stream:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise RuntimeError("모델 다운로드가 취소되었습니다.")
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        stream.write(chunk)
                        digest.update(chunk)
                        received += len(chunk)
                        if on_progress is not None:
                            on_progress(
                                EmbeddedDownloadProgress(
                                    "downloading",
                                    received,
                                    total,
                                )
                            )
                    stream.flush()
                    os.fsync(stream.fileno())
            spec = _find_spec(model, self._catalog_path)
            if spec.sha256 and digest.hexdigest().lower() != spec.sha256:
                raise RuntimeError("모델 파일 SHA-256 검증에 실패했습니다.")
            os.replace(temp_path, plan.target)
        except Exception as exc:
            try:
                temp_path.unlink()
            except OSError:
                pass
            if isinstance(exc, HTTPError):
                if exc.code == 404:
                    message = "배포처에서 모델 파일을 찾을 수 없습니다(HTTP 404). 앱을 최신 버전으로 업데이트하거나 다른 모델을 선택하세요."
                elif exc.code in {401, 403}:
                    message = f"배포처에서 모델 다운로드 접근을 제한했습니다(HTTP {exc.code}). 접근 권한·이용 조건을 확인하거나 다른 모델을 선택하세요."
                else:
                    message = f"모델 배포 서버 오류(HTTP {exc.code})입니다. 잠시 후 다시 시도하세요."
                raise RuntimeError(message) from None
            raise
        settings = load_settings(self._settings_path)
        settings.selected_model = plan.model_id
        settings.background_model = plan.model_id
        settings.manual_model = settings.manual_model or plan.model_id
        settings.summary_provider = "local"
        save_settings(settings, self._settings_path)
        if on_progress is not None:
            on_progress(EmbeddedDownloadProgress("complete", received, received))
        return plan.target

    def delete(self, model: str) -> bool:
        settings = load_settings(self._settings_path)
        spec = _find_spec(model, self._catalog_path)
        target = self._model_path(spec.model_id)
        if not target.is_file():
            raise ValueError("설치된 내장 로컬 AI 모델이 아닙니다.")
        trash = target.with_suffix(target.suffix + ".deleted")
        shutil.move(str(target), str(trash))
        cleared = False
        for field in ("selected_model", "background_model", "manual_model"):
            if getattr(settings, field).casefold() == spec.model_id.casefold():
                setattr(settings, field, "")
                cleared = True
        save_settings(settings, self._settings_path)
        return cleared

    def _model_path(self, model: str) -> Path:
        return model_path_for_id(model, self._model_dir)


def _entry_from_spec(spec: ModelSpec, target: Path) -> EmbeddedModelEntry:
    size_gb = target.stat().st_size / (1024 ** 3) if target.is_file() else 0.0
    return EmbeddedModelEntry(
        model_id=spec.model_id,
        label=spec.label,
        path=target,
        installed=target.is_file(),
        installed_size_gb=round(size_gb, 2),
        estimated_download_gb=spec.download_gb,
        download_available=bool(spec.download_url),
        parameter_size=f"{spec.parameters_b:g}B",
        usage_guidance=model_usage_guidance(
            spec.model_id,
            spec.parameters_b,
        ).display_text(),
    )


def _find_spec(model: str, catalog_path: Path | None) -> ModelSpec:
    key = model.strip().casefold().removesuffix(":latest")
    for spec in load_model_catalog(catalog_path)[1]:
        if spec.model_id.casefold().removesuffix(":latest") == key:
            return spec
    raise ValueError("오프라인 카탈로그에 없는 모델입니다.")


def _content_length(response: object) -> int | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        value = headers.get("Content-Length")
    except AttributeError:
        return None
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
