"""Application services that connect UI actions to core and infrastructure."""

from .ai_settings import AiSettingsController, AiSettingsView
from .analysis_queue import AnalysisQueueItem, AnalysisQueueStore
from .cloud_metadata_sync import (
    CloudMetadataSynchronizer,
    CloudSyncOutcome,
    MetadataConflict,
)
from .library_workflow import (
    EditablePaperMetadata,
    LibraryEntry,
    LibraryWorkflowController,
    ReviewItem,
    ReviewScan,
)
from .legacy_migration import (
    LegacyMigrationCandidate,
    LegacyMigrationPreview,
    LegacyMigrationResult,
    LegacyMigrationService,
    LegacyMigrationTrashEntry,
)
from .lifecycle import (
    LifecycleSettingsController,
    LifecycleSettingsError,
    WindowsLoginStartup,
)
from .summary_service import (
    PreparedSummary,
    ImmediateSummaryController,
    SummaryExecution,
    SummaryMode,
    SummaryPreview,
    prepare_summary,
    run_prepared_summary,
)

__all__ = [
    "AiSettingsController",
    "AiSettingsView",
    "AnalysisQueueItem",
    "AnalysisQueueStore",
    "CloudMetadataSynchronizer",
    "CloudSyncOutcome",
    "MetadataConflict",
    "EditablePaperMetadata",
    "LibraryEntry",
    "LibraryWorkflowController",
    "ReviewItem",
    "ReviewScan",
    "LegacyMigrationCandidate",
    "LegacyMigrationPreview",
    "LegacyMigrationResult",
    "LegacyMigrationService",
    "LegacyMigrationTrashEntry",
    "LifecycleSettingsController",
    "LifecycleSettingsError",
    "WindowsLoginStartup",
    "PreparedSummary",
    "ImmediateSummaryController",
    "SummaryExecution",
    "SummaryMode",
    "SummaryPreview",
    "prepare_summary",
    "run_prepared_summary",
]
