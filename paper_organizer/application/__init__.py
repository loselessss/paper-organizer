"""Application services that connect UI actions to core and infrastructure."""

from .ai_settings import AiSettingsController, AiSettingsView
from .ai_execution import (
    AiExecutionCancelled,
    AiExecutionQueue,
    AiExecutionTask,
    global_ai_execution_queue,
)
from .analysis_queue import AnalysisQueueItem, AnalysisQueueStore
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
from .local_ai import LocalAiAssessment, LocalAiAssessmentService
from .ollama_model_manager import (
    OllamaInstallPlan,
    OllamaInstallResult,
    OllamaModelEntry,
    OllamaModelManagerService,
    OllamaModelSnapshot,
)
from .summary_service import (
    PreparedSummary,
    SummaryController,
    SummaryExecution,
    SummaryMode,
    SummaryPreview,
    prepare_summary,
    run_prepared_summary,
)
from .background_analysis import (
    AnalysisReadiness,
    AnalysisRunEvent,
    BackgroundAnalysisService,
    poll_interval_seconds,
)
from .conversational_search import (
    ConversationalSearchController,
    ConversationalSearchError,
    ConversationalSearchResult,
    PreparedSearch,
    SearchCandidate,
    SearchProviderView,
    requires_ai_search,
)

__all__ = [
    "AiSettingsController",
    "AiSettingsView",
    "AiExecutionCancelled",
    "AiExecutionQueue",
    "AiExecutionTask",
    "AnalysisQueueItem",
    "AnalysisQueueStore",
    "AnalysisReadiness",
    "AnalysisRunEvent",
    "BackgroundAnalysisService",
    "ConversationalSearchController",
    "ConversationalSearchError",
    "ConversationalSearchResult",
    "poll_interval_seconds",
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
    "LocalAiAssessment",
    "LocalAiAssessmentService",
    "OllamaInstallPlan",
    "OllamaInstallResult",
    "OllamaModelEntry",
    "OllamaModelManagerService",
    "OllamaModelSnapshot",
    "PreparedSummary",
    "PreparedSearch",
    "SearchCandidate",
    "SearchProviderView",
    "requires_ai_search",
    "global_ai_execution_queue",
    "SummaryController",
    "SummaryExecution",
    "SummaryMode",
    "SummaryPreview",
    "prepare_summary",
    "run_prepared_summary",
]
