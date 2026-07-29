import json
import inspect
import unittest
from typing import Any, Mapping
from urllib.request import Request

from paper_organizer.providers import (
    AnthropicProvider,
    BibliographyRequest,
    CloudConsentRequiredError,
    OllamaProvider,
    OpenAIProvider,
    ProviderError,
    SearchAnswerRequest,
    SearchPlanRequest,
    SummaryRequest,
    cloud_request_policy,
)
from paper_organizer.infra.settings import AppSettings
from paper_organizer.providers.http import (
    UrllibJsonHttpClient,
    _CredentialSafeRedirectHandler,
)
from paper_organizer.providers.base import parse_bibliography_json, parse_summary_json


SUMMARY = {
    "summary": "요약",
    "research_question": "질문",
    "methods": ["방법"],
    "contributions": ["기여"],
    "limitations": ["한계"],
    "keywords": ["키워드"],
    "category": "생물공학",
    "subcategory": "단백질공학",
    "meta_tags": ["directed evolution", "enzyme engineering"],
    "suggested_category": "",
}
BIBLIOGRAPHY = {
    "title": "Directed evolution of a thermostable enzyme",
    "authors": ["A. Researcher"],
    "year": "2019",
    "venue": "Journal of Molecular Biology",
}
SEARCH_PLAN = {
    "search_queries": ["thermostable enzyme", "directed evolution"],
    "category": "",
    "year_from": "2020",
    "year_to": "",
}
SEARCH_ANSWER = {
    "answer_ko": "관련 논문이 있습니다.",
    "papers": [
        {
            "file_id": "sha256:test",
            "pages": [1, 2],
            "why": "열안정성 효소 실험을 보고합니다.",
        }
    ],
    "confidence": "high",
}


class FakeHttpClient:
    def __init__(self, response: Mapping[str, Any]):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post_json(self, url, headers, payload, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self.response


class ProviderTests(unittest.TestCase):
    def test_ollama_keep_alive_is_sent_to_every_request_shape(self):
        summary_client = FakeHttpClient(
            {"message": {"content": json.dumps(SUMMARY)}}
        )
        search_client = FakeHttpClient(
            {"message": {"content": json.dumps(SEARCH_PLAN)}}
        )
        OllamaProvider(
            "qwen3:4b",
            http_client=summary_client,
            keep_alive="30m",
        ).summarize(SummaryRequest("paper"))
        OllamaProvider(
            "qwen3:4b",
            http_client=search_client,
            keep_alive="30m",
        ).plan_search(SearchPlanRequest("question"))

        self.assertEqual(summary_client.calls[0]["payload"]["keep_alive"], "30m")
        self.assertEqual(search_client.calls[0]["payload"]["keep_alive"], "30m")

    def test_summary_parser_recovers_json_wrapped_in_markdown(self):
        parsed = parse_summary_json(
            "Here is the result:\n```json\n"
            + json.dumps(SUMMARY, ensure_ascii=False)
            + "\n```\n"
        )
        self.assertEqual(parsed.title, "")
        self.assertEqual(parsed.methods, ("방법",))

    def test_bibliography_parser_requires_exact_small_schema(self):
        parsed = parse_bibliography_json(json.dumps(BIBLIOGRAPHY))
        self.assertEqual(parsed.title, BIBLIOGRAPHY["title"])
        self.assertEqual(parsed.authors, ("A. Researcher",))

    def test_summary_parser_rejects_truncated_json(self):
        with self.assertRaisesRegex(ProviderError, "invalid JSON"):
            parse_summary_json('{"summary": "unfinished"')

    def test_section_summary_uses_plain_text_for_every_provider(self):
        section = "Section evidence with exact numeric values."
        cases = (
            (
                OpenAIProvider,
                FakeHttpClient(
                    {
                        "output": [
                            {
                                "type": "message",
                                "content": [
                                    {
                                        "type": "output_text",
                                        "text": section,
                                    }
                                ],
                            }
                        ]
                    }
                ),
                "openai",
            ),
            (
                AnthropicProvider,
                FakeHttpClient(
                    {
                        "content": [
                            {"type": "text", "text": section}
                        ]
                    }
                ),
                "anthropic",
            ),
            (
                OllamaProvider,
                FakeHttpClient(
                    {"message": {"content": section}}
                ),
                "ollama",
            ),
        )
        for provider_class, client, provider_name in cases:
            with self.subTest(provider=provider_name):
                provider = (
                    provider_class("secret", http_client=client)
                    if provider_name != "ollama"
                    else provider_class("qwen3:4b", http_client=client)
                )
                result = provider.summarize(
                    SummaryRequest(
                        "paper section",
                        cloud_consent=provider_name != "ollama",
                        stage="section",
                    )
                )
                payload = client.calls[0]["payload"]
                if provider_name == "openai":
                    self.assertNotIn("text", payload)
                elif provider_name == "anthropic":
                    self.assertNotIn("output_config", payload)
                else:
                    self.assertNotIn("format", payload)
                self.assertEqual(result.data.summary, section)
                self.assertEqual(result.data.methods, ())

    def test_bibliography_uses_dedicated_small_schema_for_every_provider(self):
        encoded = json.dumps(BIBLIOGRAPHY)
        cases = (
            (
                OpenAIProvider("secret", http_client=FakeHttpClient(
                    {
                        "output": [{
                            "type": "message",
                            "content": [{"type": "output_text", "text": encoded}],
                        }]
                    }
                )),
                True,
            ),
            (
                AnthropicProvider("secret", http_client=FakeHttpClient(
                    {"content": [{"type": "text", "text": encoded}]}
                )),
                True,
            ),
            (
                OllamaProvider("qwen3:4b", http_client=FakeHttpClient(
                    {"message": {"content": encoded}}
                )),
                False,
            ),
        )
        for provider, cloud_consent in cases:
            with self.subTest(provider=provider.name):
                result = provider.extract_bibliography(
                    BibliographyRequest(
                        "First-page identity text",
                        cloud_consent=cloud_consent,
                    )
                )
                payload = provider._http.calls[0]["payload"]
                if provider.name == "openai":
                    schema = payload["text"]["format"]["schema"]
                elif provider.name == "anthropic":
                    schema = payload["output_config"]["format"]["schema"]
                else:
                    schema = payload["format"]
                self.assertEqual(
                    schema["required"],
                    ["title", "authors", "year", "venue"],
                )
                self.assertEqual(result.data.venue, BIBLIOGRAPHY["venue"])
                instructions = (
                    payload["messages"][0]["content"]
                    if provider.name == "ollama"
                    else (
                        payload["instructions"]
                        if provider.name == "openai"
                        else payload["system"]
                    )
                )
                self.assertIn("ResearchGate", instructions)
                self.assertIn("Reviews and meta-analyses still have authors", instructions)

    def test_compact_summary_omits_advanced_fields_for_every_provider(self):
        compact = {
            name: value
            for name, value in SUMMARY.items()
            if name not in {"contributions", "limitations"}
        }
        encoded = json.dumps(compact)
        cases = (
            (
                OpenAIProvider("secret", http_client=FakeHttpClient(
                    {
                        "output": [{
                            "type": "message",
                            "content": [{"type": "output_text", "text": encoded}],
                        }]
                    }
                )),
                True,
            ),
            (
                AnthropicProvider("secret", http_client=FakeHttpClient(
                    {"content": [{"type": "text", "text": encoded}]}
                )),
                True,
            ),
            (
                OllamaProvider("qwen3:4b", http_client=FakeHttpClient(
                    {"message": {"content": encoded}}
                )),
                False,
            ),
        )
        for provider, cloud_consent in cases:
            with self.subTest(provider=provider.name):
                result = provider.summarize(
                    SummaryRequest(
                        "paper evidence",
                        cloud_consent=cloud_consent,
                        advanced_analysis=False,
                    )
                )
                payload = provider._http.calls[0]["payload"]
                if provider.name == "openai":
                    schema = payload["text"]["format"]["schema"]
                elif provider.name == "anthropic":
                    schema = payload["output_config"]["format"]["schema"]
                else:
                    schema = payload["format"]
                self.assertNotIn("contributions", schema["properties"])
                self.assertNotIn("limitations", schema["properties"])
                self.assertEqual(result.data.contributions, ())
                self.assertEqual(result.data.limitations, ())

    def test_openai_supports_search_plan_and_grounded_answer(self):
        plan_client = FakeHttpClient(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(SEARCH_PLAN),
                            }
                        ],
                    }
                ]
            }
        )
        plan = OpenAIProvider("secret", http_client=plan_client).plan_search(
            SearchPlanRequest("질문", cloud_consent=True)
        )
        answer_client = FakeHttpClient(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(SEARCH_ANSWER),
                            }
                        ],
                    }
                ]
            }
        )
        answer = OpenAIProvider("secret", http_client=answer_client).answer_search(
            SearchAnswerRequest(
                "질문",
                "context",
                ("sha256:test",),
                cloud_consent=True,
            )
        )

        self.assertEqual(plan.data.year_from, "2020")
        self.assertEqual(answer.data.papers[0].pages, (1, 2))
        self.assertEqual(
            plan_client.calls[0]["payload"]["text"]["format"]["name"],
            "paper_search_plan",
        )
        self.assertEqual(
            answer_client.calls[0]["payload"]["text"]["format"]["name"],
            "paper_search_answer",
        )

    def test_anthropic_supports_search_plan_and_grounded_answer(self):
        plan_client = FakeHttpClient(
            {"content": [{"type": "text", "text": json.dumps(SEARCH_PLAN)}]}
        )
        plan = AnthropicProvider("secret", http_client=plan_client).plan_search(
            SearchPlanRequest("질문", cloud_consent=True)
        )
        answer_client = FakeHttpClient(
            {"content": [{"type": "text", "text": json.dumps(SEARCH_ANSWER)}]}
        )
        answer = AnthropicProvider("secret", http_client=answer_client).answer_search(
            SearchAnswerRequest(
                "질문",
                "context",
                ("sha256:test",),
                cloud_consent=True,
            )
        )

        self.assertEqual(plan.data.search_queries[0], "thermostable enzyme")
        self.assertEqual(answer.data.confidence, "high")
        self.assertEqual(
            plan_client.calls[0]["payload"]["output_config"]["format"]["schema"][
                "required"
            ],
            ["search_queries", "category", "year_from", "year_to"],
        )

    def test_ollama_supports_search_plan_and_grounded_answer(self):
        plan_client = FakeHttpClient(
            {"message": {"content": json.dumps(SEARCH_PLAN)}}
        )
        plan = OllamaProvider("qwen3:4b", http_client=plan_client).plan_search(
            SearchPlanRequest("질문")
        )
        answer_client = FakeHttpClient(
            {"message": {"content": json.dumps(SEARCH_ANSWER)}}
        )
        answer = OllamaProvider("qwen3:4b", http_client=answer_client).answer_search(
            SearchAnswerRequest(
                "질문",
                "context",
                ("sha256:test",),
                context_window=24_576,
            )
        )

        self.assertEqual(plan.data.category, "")
        self.assertEqual(answer.data.answer_ko, "관련 논문이 있습니다.")
        self.assertFalse(plan_client.calls[0]["payload"]["think"])
        self.assertEqual(
            answer_client.calls[0]["payload"]["options"]["num_ctx"],
            24_576,
        )

    def test_openai_uses_responses_api_without_storage(self):
        client = FakeHttpClient(
            {
                "output": [
                    {
                        "type": "reasoning",
                    },
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(SUMMARY)}
                        ],
                    },
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        )
        result = OpenAIProvider("secret", http_client=client).summarize(
            SummaryRequest("paper text", cloud_consent=True)
        )

        call = client.calls[0]
        self.assertEqual(call["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(call["headers"]["Authorization"], "Bearer secret")
        self.assertFalse(call["payload"]["store"])
        self.assertEqual(call["payload"]["reasoning"], {"effort": "none"})
        self.assertEqual(
            call["payload"]["text"]["format"]["type"], "json_schema"
        )
        schema = call["payload"]["text"]["format"]["schema"]
        self.assertIn("summary", schema["required"])
        self.assertNotIn("summary_ko", schema["properties"])
        self.assertNotIn("title", schema["properties"])
        self.assertNotIn("authors", schema["properties"])
        self.assertNotIn("year", schema["properties"])
        self.assertNotIn("venue", schema["properties"])
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.data.methods, ("방법",))
        self.assertEqual(result.input_tokens, 10)

    def test_openai_requires_cloud_consent_before_http(self):
        client = FakeHttpClient({})
        with self.assertRaises(CloudConsentRequiredError):
            OpenAIProvider("secret", http_client=client).summarize(
                SummaryRequest("paper text")
            )
        self.assertEqual(client.calls, [])

    def test_anthropic_uses_messages_api_and_structured_output(self):
        client = FakeHttpClient(
            {
                "content": [{"type": "text", "text": json.dumps(SUMMARY)}],
                "usage": {"input_tokens": 30, "output_tokens": 40},
            }
        )
        result = AnthropicProvider("secret", http_client=client).summarize(
            SummaryRequest("paper text", cloud_consent=True)
        )

        call = client.calls[0]
        self.assertEqual(call["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(call["headers"]["x-api-key"], "secret")
        self.assertEqual(call["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(
            call["payload"]["output_config"]["format"]["type"], "json_schema"
        )
        self.assertEqual(result.output_tokens, 40)

    def test_ollama_does_not_require_cloud_consent(self):
        client = FakeHttpClient(
            {
                "message": {"content": json.dumps(SUMMARY)},
                "prompt_eval_count": 4,
                "eval_count": 8,
            }
        )
        result = OllamaProvider("qwen3:4b", http_client=client).summarize(
            SummaryRequest("paper text", context_window=24_576)
        )

        self.assertEqual(result.provider, "ollama")
        self.assertEqual(client.calls[0]["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(client.calls[0]["payload"]["format"]["type"], "object")
        self.assertFalse(client.calls[0]["payload"]["think"])
        self.assertEqual(client.calls[0]["payload"]["options"]["num_ctx"], 24_576)
        self.assertNotIn(
            "title",
            client.calls[0]["payload"]["format"]["properties"],
        )

    def test_invalid_provider_shape_is_rejected(self):
        broken = dict(SUMMARY)
        broken["methods"] = "not a list"
        client = FakeHttpClient(
            {"content": [{"type": "text", "text": json.dumps(broken)}]}
        )
        with self.assertRaises(ProviderError):
            AnthropicProvider("secret", http_client=client).summarize(
                SummaryRequest("paper text", cloud_consent=True)
            )

    def test_missing_api_key_is_rejected_without_http(self):
        client = FakeHttpClient({})
        with self.assertRaises(ProviderError):
            OpenAIProvider(None, http_client=client).summarize(
                SummaryRequest("paper text", cloud_consent=True)
            )
        self.assertEqual(client.calls, [])

    def test_api_key_source_is_read_at_request_time(self):
        current_key = ["first-key"]
        client = FakeHttpClient(
            {
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": json.dumps(SUMMARY)}
                        ],
                    }
                ]
            }
        )
        provider = OpenAIProvider(lambda: current_key[0], http_client=client)
        current_key[0] = "rotated-key"

        provider.summarize(SummaryRequest("paper text", cloud_consent=True))

        self.assertEqual(
            client.calls[0]["headers"]["Authorization"], "Bearer rotated-key"
        )

    def test_anthropic_admin_key_is_rejected(self):
        client = FakeHttpClient({})
        admin_key = "sk-ant-" + "admin-example-key"
        with self.assertRaisesRegex(ProviderError, "Admin API keys"):
            AnthropicProvider(admin_key, http_client=client).summarize(
                SummaryRequest("paper text", cloud_consent=True)
            )
        self.assertEqual(client.calls, [])

    def test_cloud_endpoints_are_not_constructor_options(self):
        self.assertNotIn("endpoint", inspect.signature(OpenAIProvider).parameters)
        self.assertNotIn("endpoint", inspect.signature(AnthropicProvider).parameters)

    def test_transport_rejects_credentials_for_untrusted_host(self):
        with self.assertRaisesRegex(ProviderError, "untrusted host"):
            UrllibJsonHttpClient().post_json(
                "https://example.invalid/collect",
                {"Authorization": "Bearer secret"},
                {"data": "paper text"},
                1,
            )

    def test_transport_refuses_redirect_with_credentials(self):
        request = Request(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": "Bearer secret"},
        )
        with self.assertRaisesRegex(ProviderError, "redirect"):
            _CredentialSafeRedirectHandler().redirect_request(
                request,
                None,
                307,
                "Temporary Redirect",
                {},
                "https://example.invalid/collect",
            )

    def test_high_throughput_policy_honors_explicit_parallelism(self):
        policy = cloud_request_policy(
            AppSettings(
                cloud_request_profile="high_throughput",
                cloud_max_parallel_requests=8,
                cloud_monthly_budget_usd=0,
            )
        )
        self.assertEqual(policy.max_parallel_requests, 8)
        self.assertFalse(policy.has_app_budget_cap)

    def test_standard_policy_caps_parallelism_at_two(self):
        policy = cloud_request_policy(
            AppSettings(
                cloud_request_profile="standard",
                cloud_max_parallel_requests=8,
                cloud_monthly_budget_usd=25,
            )
        )
        self.assertEqual(policy.max_parallel_requests, 2)
        self.assertEqual(policy.monthly_budget_usd, 25.0)


if __name__ == "__main__":
    unittest.main()
