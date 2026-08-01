"""Secret storage kept deliberately separate from JSON application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


SERVICE_NAME = "PaperOrganizer"
ENVIRONMENT_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class SecretStore(Protocol):
    def get(self, provider: str) -> str | None: ...

    def set(self, provider: str, secret: str) -> None: ...

    def delete(self, provider: str) -> None: ...


def delete_all_credentials(store: SecretStore) -> tuple[str, ...]:
    """Delete only Paper Organizer cloud credentials from the supplied store."""

    deleted: list[str] = []
    for provider in sorted(ENVIRONMENT_KEYS):
        store.delete(provider)
        deleted.append(provider)
    return tuple(deleted)


@dataclass(frozen=True, slots=True)
class SecretStatus:
    provider: str
    configured: bool
    masked_hint: str


def _validate_provider(provider: str) -> str:
    normalized = provider.strip().lower()
    if normalized not in ENVIRONMENT_KEYS:
        raise ValueError(f"Unsupported secret provider: {provider}")
    return normalized


def validate_api_key(provider: str, secret: str | None) -> str:
    normalized = _validate_provider(provider)
    value = (secret or "").strip()
    if not value:
        raise ValueError("API key cannot be empty")
    if normalized == "anthropic" and value.lower().startswith("sk-ant-admin"):
        raise ValueError("Anthropic Admin API keys are not accepted")
    return value


def mask_secret(secret: str | None) -> str:
    """Return a UI-safe hint without exposing enough material to reuse the key."""
    value = (secret or "").strip()
    if not value:
        return ""
    suffix = value[-4:] if len(value) >= 4 else ""
    return f"••••{suffix}" if suffix else "••••"


def get_secret_status(store: SecretStore, provider: str) -> SecretStatus:
    normalized = _validate_provider(provider)
    secret = store.get(normalized)
    return SecretStatus(
        provider=normalized,
        configured=bool(secret),
        masked_hint=mask_secret(secret),
    )


def sanitized_child_environment(
    environment: dict[str, str] | None = None,
) -> dict[str, str]:
    """Prevent development fallback keys from leaking into child processes."""
    result = dict(os.environ if environment is None else environment)
    for variable_name in ENVIRONMENT_KEYS.values():
        result.pop(variable_name, None)
    return result


class EnvironmentSecretStore:
    """Read-only fallback for development and managed deployments."""

    def get(self, provider: str) -> str | None:
        name = ENVIRONMENT_KEYS[_validate_provider(provider)]
        value = os.environ.get(name, "").strip()
        return value or None

    def set(self, provider: str, secret: str) -> None:
        _validate_provider(provider)
        raise RuntimeError("Environment secrets must be configured outside the app")

    def delete(self, provider: str) -> None:
        _validate_provider(provider)
        raise RuntimeError("Environment secrets must be removed outside the app")


class KeyringSecretStore:
    """Use the OS credential backend exposed by the optional keyring package."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        try:
            import keyring
        except ImportError as exc:
            raise RuntimeError(
                "Secure key storage requires the optional 'cloud' dependency"
            ) from exc
        self._keyring = keyring
        self._service_name = service_name

    def get(self, provider: str) -> str | None:
        value = self._keyring.get_password(
            self._service_name, _validate_provider(provider)
        )
        return value.strip() if value and value.strip() else None

    def set(self, provider: str, secret: str) -> None:
        normalized = _validate_provider(provider)
        value = validate_api_key(normalized, secret)
        self._keyring.set_password(self._service_name, normalized, value)

    def delete(self, provider: str) -> None:
        normalized = _validate_provider(provider)
        try:
            self._keyring.delete_password(self._service_name, normalized)
        except self._keyring.errors.PasswordDeleteError:
            pass


class CompositeSecretStore:
    """Prefer OS credentials and fall back to environment variables for reads."""

    def __init__(self, primary: SecretStore, fallback: SecretStore) -> None:
        self._primary = primary
        self._fallback = fallback

    def get(self, provider: str) -> str | None:
        return self._primary.get(provider) or self._fallback.get(provider)

    def set(self, provider: str, secret: str) -> None:
        self._primary.set(provider, secret)

    def delete(self, provider: str) -> None:
        self._primary.delete(provider)


def default_secret_store() -> SecretStore:
    environment = EnvironmentSecretStore()
    try:
        return CompositeSecretStore(KeyringSecretStore(), environment)
    except RuntimeError:
        return environment
