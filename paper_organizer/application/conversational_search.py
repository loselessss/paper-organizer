"""RAG-lite natural-language search over PaperPack full-text pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Thread
from typing import Callable

from paper_organizer.application.ai_execution import (
    AI_PRIORITY_SEARCH,
    AiExecutionQueue,
    global_ai_execution_queue,
)
from paper_organizer.application.library_workflow import (
    LibraryEntry,
    LibraryWorkflowController,
)
from paper_organizer.application.summary_service import _adaptive_context_window
from paper_organizer.core.paperpack import (
    PAPERPACK_SUFFIX,
    content_pages,
    load_paperpack_content,
)
from paper_organizer.core.search_index import (
    SearchIndexError,
    search,
    search_metadata,
)
from paper_organizer.core.model_recommendation import load_model_catalog
from paper_organizer.infra.ollama_installer import (
    start_runtime,
    stop_managed_runtime,
)
from paper_organizer.infra.ollama_runtime import (
    OllamaRuntimeInspector,
    OllamaRuntimeStatus,
)
from paper_organizer.infra.secrets import SecretStore
from paper_organizer.infra.settings import (
    AppSettings,
    default_settings_path,
    load_settings,
)
from paper_organizer.providers.base import (
    JsonHttpClient,
    SearchAnswerData,
    SearchAnswerRequest,
    SearchPaperEvidence,
    SearchPlanData,
    SearchPlanRequest,
)
from paper_organizer.providers.registry import build_provider


MAX_CANDIDATES = 5
MAX_PAGES_PER_PAPER = 2
MAX_PAGE_CHARS = 3_500
MAX_CONTEXT_CHARS = 48_000
MAX_SEARCH_QUERIES = 12
_EXPLANATION_HINTS = (
    "비교",
    "설명",
    "요약",
    "정리",
    "차이",
    "공통",
    "왜",
    "어떻게",
    "어떤",
    "무엇",
    "근거",
    "알려줘",
    "찾아줘",
    "이후",
    "이전",
    "compare",
    "explain",
    "summarize",
    "summary",
    "difference",
    "why",
    "how",
    "which",
    "what",
)
_KOREAN_SEARCH_EQUIVALENTS = {
    "배지": ("culture medium", "growth medium"),
    "세포주": ("cell line",),
    "배양": ("cell culture", "cultured"),
    "처리": ("treatment", "treated"),
    "농도": ("concentration",),
    "온도": ("temperature",),
    "항체": ("antibody",),
    "단백질": ("protein",),
    "유전자": ("gene",),
    "효소": ("enzyme",),
    "발현": ("expression",),
    "정제": ("purification",),
}


class ConversationalSearchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SearchProviderView:
    provider: str
    model: str
    sends_to_cloud: bool
    requires_cloud_consent: bool


@dataclass(frozen=True, slots=True)
class SearchCandidate:
    file_id: str
    sidecar_path: Path
    title: str
    authors: tuple[str, ...]
    year: int | None
    venue: str
    category: str
    pages: tuple[int, ...]
    excerpts: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedSearch:
    question: str
    provider: str
    model: str
    plan: SearchPlanData
    candidates: tuple[SearchCandidate, ...]
    character_count: int
    sends_to_cloud: bool
    requires_cloud_consent: bool
    context_window: int | None
    context_text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ConversationalSearchResult:
    answer: SearchAnswerData
    candidates: tuple[SearchCandidate, ...]
    provider: str
    model: str


class ConversationalSearchController:
    """Interpret a question, retrieve local evidence, then answer from it."""

    def __init__(
        self,
        workflow: LibraryWorkflowController,
        secret_store: SecretStore,
        settings_path: Path | None = None,
        http_client: JsonHttpClient | None = None,
        ollama: OllamaRuntimeInspector | None = None,
        start_local_runtime: Callable[[], bool] | None = None,
        execution_queue: AiExecutionQueue | None = None,
    ) -> None:
        self._workflow = workflow
        self._secret_store = secret_store
        self._settings_path = settings_path or default_settings_path()
        self._http_client = http_client
        self._ollama = ollama or OllamaRuntimeInspector()
        self._start_local_runtime = start_local_runtime or (
            lambda: start_runtime(inspector=self._ollama)
        )
        self._execution_queue = execution_queue or global_ai_execution_queue()

    def provider_view(self) -> SearchProviderView:
        settings = load_settings(self._settings_path)
        local_model = ""
        if self._start_local_runtime():
            local_model = _preferred_installed_model(
                settings, self._ollama.inspect()
            )
        if local_model:
            return SearchProviderView(
                provider="ollama",
                model=local_model,
                sends_to_cloud=False,
                requires_cloud_consent=False,
            )
        model = _selected_model(settings)
        cloud = settings.summary_provider in {"openai", "anthropic"}
        return SearchProviderView(
            provider=settings.summary_provider,
            model=model,
            sends_to_cloud=cloud,
            requires_cloud_consent=cloud and not settings.cloud_processing_consent,
        )

    def prepare(
        self, question: str, *, allow_cloud_once: bool = False
    ) -> PreparedSearch:
        normalized = " ".join(question.split())
        if not normalized:
            raise ConversationalSearchError("질문을 입력하세요.")
        if len(normalized) > 2_000:
            raise ConversationalSearchError("질문은 2,000자 이내로 입력하세요.")
        settings = load_settings(self._settings_path)
        settings.validate()
        view = self.provider_view()
        if not view.model:
            raise ConversationalSearchError("AI 모델을 먼저 선택하세요.")
        consent = settings.cloud_processing_consent or allow_cloud_once
        if view.provider == "ollama" and not self._start_local_runtime():
            raise ConversationalSearchError(
                "Ollama 서버를 시작할 수 없습니다. AI 설정과 설치 상태를 확인하세요."
            )
        provider_settings = _settings_for_search_provider(settings, view)
        provider = build_provider(
            provider_settings,
            self._secret_store,
            http_client=self._http_client,
        )
        try:
            with self._execution_queue.slot(
                "search_plan",
                normalized[:80],
                priority=AI_PRIORITY_SEARCH,
            ):
                plan_result = provider.plan_search(
                    SearchPlanRequest(
                        question=normalized,
                        cloud_consent=consent,
                    )
                )
            plan = plan_result.data
            candidates = self._retrieve_candidates(normalized, plan)
            context_text, candidates = _build_context(candidates)
        except Exception:
            if view.provider == "ollama":
                self._stop_local_runtime_in_queue()
            raise
        if not candidates:
            if view.provider == "ollama":
                self._stop_local_runtime_in_queue()
            return PreparedSearch(
                question=normalized,
                provider=view.provider,
                model=view.model,
                plan=plan,
                candidates=(),
                character_count=0,
                sends_to_cloud=view.sends_to_cloud,
                requires_cloud_consent=view.requires_cloud_consent,
                context_window=None,
                context_text="",
            )
        context_window = _adaptive_context_window(
            settings, view.model, len(context_text)
        )
        return PreparedSearch(
            question=normalized,
            provider=view.provider,
            model=view.model,
            plan=plan,
            candidates=candidates,
            character_count=len(context_text),
            sends_to_cloud=view.sends_to_cloud,
            requires_cloud_consent=view.requires_cloud_consent,
            context_window=context_window,
            context_text=context_text,
        )

    def answer(
        self, prepared: PreparedSearch, *, allow_cloud_once: bool = False
    ) -> ConversationalSearchResult:
        if not prepared.candidates or not prepared.context_text:
            raise ConversationalSearchError("답변할 검색 후보가 없습니다.")
        settings = load_settings(self._settings_path)
        view = self.provider_view()
        if (view.provider, view.model) != (prepared.provider, prepared.model):
            self.stop_local_runtime()
            raise ConversationalSearchError(
                "검색 준비 후 AI 제공자 또는 모델이 변경되었습니다. 다시 검색하세요."
            )
        consent = settings.cloud_processing_consent or allow_cloud_once
        provider_settings = _settings_for_search_provider(settings, view)
        provider = build_provider(
            provider_settings,
            self._secret_store,
            http_client=self._http_client,
        )
        with self._execution_queue.slot(
            "search_answer",
            prepared.question[:80],
            priority=AI_PRIORITY_SEARCH,
        ):
            try:
                result = provider.answer_search(
                    SearchAnswerRequest(
                        question=prepared.question,
                        context_text=prepared.context_text,
                        allowed_file_ids=tuple(
                            candidate.file_id for candidate in prepared.candidates
                        ),
                        cloud_consent=consent,
                        context_window=prepared.context_window,
                    )
                )
            finally:
                if view.provider == "ollama":
                    stop_managed_runtime()
        answer = _validated_answer(result.data, prepared.candidates)
        return ConversationalSearchResult(
            answer=answer,
            candidates=prepared.candidates,
            provider=result.provider,
            model=result.model,
        )

    def stop_local_runtime(self) -> None:
        Thread(
            target=self._stop_local_runtime_in_queue,
            daemon=True,
        ).start()

    def _stop_local_runtime_in_queue(self) -> None:
        with self._execution_queue.slot(
            "runtime_cleanup",
            "Ollama 검색 종료",
            priority=AI_PRIORITY_SEARCH,
        ):
            stop_managed_runtime()

    def _retrieve_candidates(
        self, question: str, plan: SearchPlanData
    ) -> tuple[SearchCandidate, ...]:
        _input_dir, root = self._workflow.configured_paths()
        entries = {
            _entry_file_id(entry): entry
            for entry in self._workflow.list_library()
            if _entry_file_id(entry)
        }
        queries = _expanded_queries(question, plan.search_queries)
        scores: dict[str, int] = {}
        matched_pages: dict[str, set[int]] = {}
        for query_index, query in enumerate(queries):
            try:
                hits = search(root, query, limit=12)
            except SearchIndexError:
                hits = []
            for rank, hit in enumerate(hits):
                if hit.file_id not in entries:
                    continue
                scores[hit.file_id] = scores.get(hit.file_id, 0) + max(
                    1, 100 - query_index * 8 - rank * 3
                )
                if hit.page > 0:
                    matched_pages.setdefault(hit.file_id, set()).add(hit.page)
            try:
                metadata_hits = search_metadata(root, query, limit=12)
            except SearchIndexError:
                metadata_hits = []
            for rank, hit in enumerate(metadata_hits):
                if hit.file_id in entries:
                    scores[hit.file_id] = scores.get(hit.file_id, 0) + max(
                        1, 30 - rank
                    )
        ranked = sorted(
            scores,
            key=lambda file_id: (-scores[file_id], entries[file_id].metadata.title.casefold()),
        )
        filtered = [
            file_id
            for file_id in ranked
            if _matches_filters(entries[file_id], plan)
        ]
        if plan.category and not filtered:
            filtered = ranked
        candidates: list[SearchCandidate] = []
        for file_id in filtered:
            entry = entries[file_id]
            candidate = _candidate_from_entry(
                file_id,
                entry,
                matched_pages.get(file_id, set()),
            )
            if candidate is not None:
                candidates.append(candidate)
            if len(candidates) == MAX_CANDIDATES:
                break
        return tuple(candidates)


def requires_ai_search(query: str) -> bool:
    """Route short literal lookups to FTS and explanatory questions to AI."""

    normalized = " ".join(query.casefold().split())
    if not normalized:
        return False
    if "?" in normalized or any(hint in normalized for hint in _EXPLANATION_HINTS):
        return True
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#:/-]*|[가-힣]+", normalized)
    return len(tokens) > 5


def _entry_file_id(entry: LibraryEntry) -> str:
    return str(
        entry.record.get("identity", {}).get("file_id")
        or entry.record.get("id")
        or ""
    )


def _selected_model(settings: AppSettings) -> str:
    if settings.summary_provider == "ollama":
        return settings.selected_model.strip()
    if settings.summary_provider == "openai":
        return settings.openai_model.strip()
    return settings.anthropic_model.strip()


def _settings_for_search_provider(
    settings: AppSettings, view: SearchProviderView
) -> AppSettings:
    if view.provider != "ollama":
        return settings
    return replace(
        settings,
        summary_provider="ollama",
        selected_model=view.model,
    )


def _preferred_installed_model(
    settings: AppSettings, status: OllamaRuntimeStatus
) -> str:
    """Prefer an installed small model without selecting or downloading it."""

    if not status.reachable or not status.models:
        return ""
    _version, specs = load_model_catalog()
    parameters = {spec.model_id.casefold(): spec.parameters_b for spec in specs}

    def aliases(name: str) -> set[str]:
        key = name.strip().casefold()
        values = {key}
        if key.endswith(":latest"):
            values.add(key.removesuffix(":latest"))
        return values

    installed = [
        (
            model.name,
            aliases(model.name),
            _installed_parameters_b(model.name, model.parameter_size, parameters),
        )
        for model in status.models
    ]
    compatible = [item for item in installed if item[2] is None or item[2] <= 8.0]
    if not compatible:
        return ""
    for preferred in (settings.selected_model, settings.recommended_model):
        preferred_aliases = aliases(preferred)
        for name, model_aliases, _size in compatible:
            if preferred_aliases & model_aliases:
                return name
    search_sized = [
        item for item in compatible if item[2] is not None and 1.5 <= item[2] <= 4.0
    ]
    pool = search_sized or compatible
    return min(
        pool,
        key=lambda item: (
            item[2] is None,
            item[2] if item[2] is not None else 99.0,
            item[0].casefold(),
        ),
    )[0]


def _installed_parameters_b(
    model_name: str,
    parameter_size: str,
    catalog_parameters: dict[str, float],
) -> float | None:
    key = model_name.strip().casefold()
    for alias in (key, key.removesuffix(":latest")):
        if alias in catalog_parameters:
            return catalog_parameters[alias]
    match = re.search(r"(\d+(?:\.\d+)?)\s*[bB]\b", parameter_size)
    if match is None:
        match = re.search(r"(?<![\d.])(\d+(?:\.\d+)?)\s*[bB](?:\b|$)", model_name)
    return float(match.group(1)) if match is not None else None


def _fallback_queries(question: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#-]*|[가-힣]{2,}", question)
    ignored = {"논문", "연구", "결과", "방법", "어떤", "있나", "찾아줘", "보여줘"}
    values = [token for token in tokens if token.casefold() not in ignored]
    return values[:8] or [question]


def _expanded_queries(
    question: str, planned_queries: tuple[str, ...]
) -> list[str]:
    """Keep source identifiers and add English terms for Korean questions."""

    exact_identifiers = re.findall(
        r"(?<![A-Za-z0-9.+#:/-])"
        r"(?=[A-Za-z0-9.+#:/-]*[A-Z0-9])"
        r"[A-Za-z0-9][A-Za-z0-9.+#:/-]*"
        r"(?![A-Za-z0-9.+#:/-])",
        question,
    )
    english_equivalents = [
        query
        for korean, queries in _KOREAN_SEARCH_EQUIVALENTS.items()
        if korean in question
        for query in queries
    ]
    candidates = [
        *exact_identifiers,
        *planned_queries,
        *english_equivalents,
        *_fallback_queries(question),
    ]
    expanded: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = " ".join(candidate.split())
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        expanded.append(normalized)
        if len(expanded) == MAX_SEARCH_QUERIES:
            break
    return expanded


def _matches_filters(entry: LibraryEntry, plan: SearchPlanData) -> bool:
    metadata = entry.metadata
    if plan.category:
        category_text = f"{metadata.category} {metadata.subcategory}".casefold()
        if plan.category.casefold() not in category_text:
            return False
    year = metadata.year
    year_from = _year(plan.year_from)
    year_to = _year(plan.year_to)
    if year is not None and year_from is not None and year < year_from:
        return False
    if year is not None and year_to is not None and year > year_to:
        return False
    return True


def _year(value: str) -> int | None:
    cleaned = value.strip()
    return int(cleaned) if len(cleaned) == 4 and cleaned.isdigit() else None


def _candidate_from_entry(
    file_id: str, entry: LibraryEntry, matched_pages: set[int]
) -> SearchCandidate | None:
    if (
        entry.sidecar_path.suffix.casefold() != PAPERPACK_SUFFIX
        or not entry.sidecar_path.is_file()
    ):
        return None
    try:
        pages = content_pages(load_paperpack_content(entry.sidecar_path))
    except Exception:
        return None
    if not pages:
        return None
    page_map = {number: " ".join(text.split()) for number, text in pages if text.strip()}
    selected = [page for page in sorted(matched_pages) if page in page_map]
    if not selected:
        selected = [next(iter(page_map))]
    selected = selected[:MAX_PAGES_PER_PAPER]
    excerpts = tuple(page_map[page][:MAX_PAGE_CHARS] for page in selected)
    metadata = entry.metadata
    return SearchCandidate(
        file_id=file_id,
        sidecar_path=entry.sidecar_path,
        title=metadata.title,
        authors=tuple(metadata.authors),
        year=metadata.year,
        venue=metadata.venue,
        category=f"{metadata.category} / {metadata.subcategory}",
        pages=tuple(selected),
        excerpts=excerpts,
    )


def _build_context(
    candidates: tuple[SearchCandidate, ...],
) -> tuple[str, tuple[SearchCandidate, ...]]:
    blocks: list[str] = []
    included: list[SearchCandidate] = []
    total = 0
    for index, candidate in enumerate(candidates, start=1):
        lines = [
            f"[PAPER {index}]",
            f"file_id: {candidate.file_id}",
            f"title: {candidate.title}",
            f"authors: {', '.join(candidate.authors)}",
            f"year: {candidate.year or ''}",
            f"venue: {candidate.venue}",
            f"category: {candidate.category}",
        ]
        for page, excerpt in zip(candidate.pages, candidate.excerpts):
            lines.extend((f"[PDF PAGE {page}]", excerpt))
        block = "\n".join(lines)
        if total + len(block) > MAX_CONTEXT_CHARS:
            break
        blocks.append(block)
        included.append(candidate)
        total += len(block)
    return "\n\n".join(blocks), tuple(included)


def _validated_answer(
    answer: SearchAnswerData, candidates: tuple[SearchCandidate, ...]
) -> SearchAnswerData:
    allowed = {candidate.file_id: set(candidate.pages) for candidate in candidates}
    evidence: list[SearchPaperEvidence] = []
    seen: set[str] = set()
    for paper in answer.papers:
        if paper.file_id not in allowed or paper.file_id in seen:
            continue
        pages = tuple(page for page in paper.pages if page in allowed[paper.file_id])
        if not pages:
            continue
        seen.add(paper.file_id)
        evidence.append(
            SearchPaperEvidence(
                file_id=paper.file_id,
                pages=pages,
                why=paper.why,
            )
        )
    confidence = answer.confidence if evidence else "low"
    return SearchAnswerData(
        answer_ko=answer.answer_ko,
        papers=tuple(evidence),
        confidence=confidence,
    )
