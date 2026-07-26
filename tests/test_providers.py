import json
import inspect
import unittest
from typing import Any, Mapping
from urllib.request import Request

from paper_organizer.providers import (
    AnthropicProvider,
    CloudConsentRequiredError,
    OllamaProvider,
    OpenAIProvider,
    ProviderError,
    SummaryRequest,
    cloud_request_policy,
)
from paper_organizer.infra.settings import AppSettings
from paper_organizer.providers.http import (
    UrllibJsonHttpClient,
    _CredentialSafeRedirectHandler,
)


SUMMARY = {
    "summary_ko": "요약",
    "research_question": "질문",
    "methods": ["방법"],
    "contributions": ["기여"],
    "limitations": ["한계"],
    "keywords": ["키워드"],
    "title": "Directed evolution of a thermostable enzyme",
    "authors": ["A. Researcher"],
    "year": "2019",
    "venue": "Journal of Molecular Biology",
    "category": "생물공학",
    "subcategory": "단백질공학",
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
            SummaryRequest("paper text")
        )

        self.assertEqual(result.provider, "ollama")
        self.assertEqual(client.calls[0]["url"], "http://127.0.0.1:11434/api/chat")
        self.assertEqual(client.calls[0]["payload"]["format"]["type"], "object")
        self.assertFalse(client.calls[0]["payload"]["think"])
        self.assertIn(
            "copy those fields verbatim",
            client.calls[0]["payload"]["messages"][0]["content"],
        )
        self.assertIn(
            "never treat a review article as authorless",
            client.calls[0]["payload"]["messages"][0]["content"].casefold(),
        )
        self.assertIn(
            "for patents",
            client.calls[0]["payload"]["messages"][0]["content"].casefold(),
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
