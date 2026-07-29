"""Coordinate catalog, disk safety and explicit Ollama model lifecycle actions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from paper_organizer.core.model_recommendation import (
    ModelSpec,
    load_model_catalog,
    model_usage_guidance,
)
from paper_organizer.infra.hardware import HardwareInspector
from paper_organizer.infra.ollama_models import (
    OllamaModelClient,
    OllamaPullProgress,
    OllamaVerification,
    validate_model_name,
)
from paper_organizer.infra.ollama_runtime import (
    InstalledOllamaModel,
    OllamaRuntimeInspector,
)
from paper_organizer.infra.settings import (
    default_settings_path,
    load_settings,
    save_settings,
)


@dataclass(frozen=True, slots=True)
class OllamaModelEntry:
    model_id: str
    label: str
    estimated_download_gb: float | None
    installed: bool
    installed_size_gb: float
    parameter_size: str
    quantization: str
    managed_by_app: bool
    selectable: bool = True
    usage_guidance: str = ""


@dataclass(frozen=True, slots=True)
class OllamaModelSnapshot:
    reachable: bool
    version: str
    disk_path: str
    disk_free_gb: float
    entries: tuple[OllamaModelEntry, ...]
    error: str = ""


@dataclass(frozen=True, slots=True)
class OllamaInstallPlan:
    model_id: str
    label: str
    estimated_download_gb: float
    required_free_gb: float
    available_free_gb: float
    already_installed: bool
    can_install: bool
    reason: str


@dataclass(frozen=True, slots=True)
class OllamaInstallResult:
    verification: OllamaVerification
    newly_managed: bool


class OllamaModelManagerService:
    def __init__(
        self,
        settings_path: Path | None = None,
        *,
        hardware: HardwareInspector | None = None,
        runtime: OllamaRuntimeInspector | None = None,
        client: OllamaModelClient | None = None,
        catalog_path: Path | None = None,
    ) -> None:
        self._settings_path = settings_path or default_settings_path()
        self._hardware = hardware or HardwareInspector()
        self._runtime = runtime or OllamaRuntimeInspector()
        self._client = client or OllamaModelClient()
        self._catalog_path = catalog_path

    def snapshot(self) -> OllamaModelSnapshot:
        hardware = self._hardware.inspect()
        runtime = self._runtime.inspect()
        _, specs = load_model_catalog(self._catalog_path)
        settings = load_settings(self._settings_path)
        managed = {name.casefold() for name in settings.managed_ollama_models}
        installed = {_model_key(model.name): model for model in runtime.models}
        entries: list[OllamaModelEntry] = []
        catalog_keys: set[str] = set()
        for spec in specs:
            key = _model_key(spec.model_id)
            catalog_keys.add(key)
            actual = installed.get(key)
            entries.append(_entry_from_spec(spec, actual, key in managed))
        for key, actual in installed.items():
            if key in catalog_keys:
                continue
            entries.append(
                OllamaModelEntry(
                    model_id=actual.name,
                    label=actual.name,
                    estimated_download_gb=None,
                    installed=True,
                    installed_size_gb=actual.size_gb,
                    parameter_size=actual.parameter_size,
                    quantization=actual.quantization,
                    managed_by_app=key in managed,
                    selectable=_installed_model_is_selectable(actual),
                    usage_guidance=model_usage_guidance(
                        actual.name,
                        _installed_parameters_b(actual),
                    ).display_text(),
                )
            )
        return OllamaModelSnapshot(
            reachable=runtime.reachable,
            version=runtime.version,
            disk_path=hardware.model_disk_path,
            disk_free_gb=hardware.model_disk_free_gb,
            entries=tuple(entries),
            error=runtime.error,
        )

    def installed_models(self) -> tuple[str, ...]:
        """Return installed models that this app permits as summary engines."""

        runtime = self._runtime.inspect()
        if not runtime.reachable:
            message = runtime.error or "Ollama에 연결할 수 없습니다."
            raise RuntimeError(message)
        return tuple(
            sorted(
                (
                    model.name
                    for model in runtime.models
                    if _installed_model_is_selectable(model)
                ),
                key=str.casefold,
            )
        )

    def plan_install(self, model: str) -> OllamaInstallPlan:
        model_id = validate_model_name(model)
        snapshot = self.snapshot()
        if not snapshot.reachable:
            return OllamaInstallPlan(
                model_id, model_id, 0, 0, snapshot.disk_free_gb, False, False,
                "Ollama가 실행 중이 아니거나 로컬 API에 연결할 수 없습니다.",
            )
        _, specs = load_model_catalog(self._catalog_path)
        spec = next(
            (item for item in specs if _model_key(item.model_id) == _model_key(model_id)),
            None,
        )
        if spec is None:
            return OllamaInstallPlan(
                model_id, model_id, 0, 0, snapshot.disk_free_gb, False, False,
                "오프라인 카탈로그에 없는 모델은 크기를 확인할 수 없어 앱에서 다운로드하지 않습니다.",
            )
        entry = next(
            item for item in snapshot.entries if _model_key(item.model_id) == _model_key(model_id)
        )
        required = round(spec.download_gb * 1.5 + 2.0, 2)
        enough = snapshot.disk_free_gb >= required
        installed = entry.installed
        reason = (
            "이미 설치되어 있어 다시 다운로드할 필요가 없습니다."
            if installed
            else "다운로드와 임시 파일을 위한 안전 여유가 충분합니다."
            if enough
            else f"안전한 설치에는 최소 {required:g}GB의 여유 공간이 필요합니다."
        )
        return OllamaInstallPlan(
            model_id=spec.model_id,
            label=spec.label,
            estimated_download_gb=spec.download_gb,
            required_free_gb=required,
            available_free_gb=snapshot.disk_free_gb,
            already_installed=installed,
            can_install=not installed and enough,
            reason=reason,
        )

    def install(
        self,
        model: str,
        *,
        on_progress=None,
        cancel: Event | None = None,
    ) -> OllamaInstallResult:
        plan = self.plan_install(model)
        if not plan.can_install:
            raise ValueError(plan.reason)
        self._client.pull(
            plan.model_id,
            on_progress=on_progress,
            cancel=cancel,
        )
        verification = self._client.verify(plan.model_id)
        settings = load_settings(self._settings_path)
        known = {name.casefold() for name in settings.managed_ollama_models}
        newly_managed = plan.model_id.casefold() not in known
        if newly_managed:
            settings.managed_ollama_models.append(plan.model_id)
            settings.managed_ollama_models.sort(key=str.casefold)
            save_settings(settings, self._settings_path)
        return OllamaInstallResult(verification, newly_managed)

    def verify_installed(self, model: str) -> OllamaVerification:
        model_id = validate_model_name(model)
        snapshot = self.snapshot()
        entry = next(
            (
                item
                for item in snapshot.entries
                if _model_key(item.model_id) == _model_key(model_id) and item.installed
            ),
            None,
        )
        if entry is None:
            raise ValueError("설치된 Ollama 모델이 아닙니다.")
        return self._client.verify(entry.model_id)

    def delete(self, model: str) -> bool:
        model_id = validate_model_name(model)
        snapshot = self.snapshot()
        entry = next(
            (
                item
                for item in snapshot.entries
                if _model_key(item.model_id) == _model_key(model_id) and item.installed
            ),
            None,
        )
        if entry is None:
            raise ValueError("설치된 Ollama 모델이 아닙니다.")
        self._client.delete(entry.model_id)
        settings = load_settings(self._settings_path)
        settings.managed_ollama_models = [
            name
            for name in settings.managed_ollama_models
            if _model_key(name) != _model_key(entry.model_id)
        ]
        selection_cleared = _model_key(settings.selected_model) == _model_key(entry.model_id)
        if selection_cleared:
            settings.selected_model = ""
        save_settings(settings, self._settings_path)
        return selection_cleared


def _entry_from_spec(
    spec: ModelSpec,
    actual: InstalledOllamaModel | None,
    managed: bool,
) -> OllamaModelEntry:
    return OllamaModelEntry(
        model_id=spec.model_id,
        label=spec.label,
        estimated_download_gb=spec.download_gb,
        installed=actual is not None,
        installed_size_gb=actual.size_gb if actual is not None else 0.0,
        parameter_size=actual.parameter_size if actual is not None else "",
        quantization=actual.quantization if actual is not None else "",
        managed_by_app=managed,
        usage_guidance=model_usage_guidance(
            spec.model_id,
            spec.parameters_b,
        ).display_text(),
    )


def _model_key(value: str) -> str:
    key = value.strip().casefold()
    return key.removesuffix(":latest")


def _installed_model_is_selectable(model: InstalledOllamaModel) -> bool:
    """Keep already-installed 12B+ models visible for inventory, not selection."""

    description = f"{model.parameter_size} {model.name}".casefold()
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?:\b|$)", description)
    return match is None or float(match.group(1)) < 12


def _installed_parameters_b(model: InstalledOllamaModel) -> float | None:
    description = f"{model.parameter_size} {model.name}".casefold()
    match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b(?:\b|$)", description)
    return float(match.group(1)) if match else None
