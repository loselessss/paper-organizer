"""Cloud queue limits derived from user settings."""

from __future__ import annotations

from dataclasses import dataclass

from paper_organizer.infra.settings import AppSettings


@dataclass(frozen=True, slots=True)
class CloudRequestPolicy:
    profile: str
    max_parallel_requests: int
    monthly_budget_usd: float | None

    @property
    def has_app_budget_cap(self) -> bool:
        return self.monthly_budget_usd is not None


def cloud_request_policy(settings: AppSettings) -> CloudRequestPolicy:
    """Return effective limits; a zero budget means no app-enforced cost cap."""
    settings.validate()
    if settings.cloud_request_profile == "conservative":
        parallelism = 1
    elif settings.cloud_request_profile == "standard":
        parallelism = min(settings.cloud_max_parallel_requests, 2)
    else:
        parallelism = settings.cloud_max_parallel_requests
    budget = (
        float(settings.cloud_monthly_budget_usd)
        if settings.cloud_monthly_budget_usd > 0
        else None
    )
    return CloudRequestPolicy(
        profile=settings.cloud_request_profile,
        max_parallel_requests=parallelism,
        monthly_budget_usd=budget,
    )
