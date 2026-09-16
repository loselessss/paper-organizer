"""Conservatively classify summaries into existing project IDs."""

import json

from paper_organizer.providers.base import ProviderError, SummaryRequest


PROMPT_VERSION = "project-classification-v1"


def classify_projects(provider, projects, *, title, summary, consent, cancelled):
    selected = set()
    for offset in range(0, len(projects), 8):
        if cancelled():
            raise ProviderError("프로젝트 분류가 취소되었습니다.")
        batch = projects[offset:offset + 8]
        result = provider.summarize(SummaryRequest(
            document_text=json.dumps({
                "paper": {"title": title[:500], "summary": summary[:4000]},
                "projects": [{"id": p["id"], "name": p["name"],
                              "description": p["description"][:1000]} for p in batch],
            }, ensure_ascii=False),
            stage="project", prompt_version=PROMPT_VERSION,
            cloud_consent=consent, max_output_tokens=512,
            context_window=8192, advanced_analysis=False,
        ))
        try:
            raw = json.loads(result.data.summary)
            ids = raw["project_ids"]
            allowed = {p["id"] for p in batch}
            if (set(raw) != {"project_ids"} or not isinstance(ids, list)
                    or any(not isinstance(key, str) or key not in allowed for key in ids)):
                raise ValueError()
        except (ValueError, TypeError, KeyError) as exc:
            raise ProviderError("프로젝트 분류 응답이 올바르지 않습니다.") from exc
        selected.update(ids)
    if cancelled():
        raise ProviderError("프로젝트 분류가 취소되었습니다.")
    return tuple(sorted(selected))
