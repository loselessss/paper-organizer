import unittest

from paper_organizer.core.benchmark_recommendation import (
    recommend_observed_model,
)


def result(
    model: str,
    score: float,
    seconds: float,
    *,
    status: str = "ok",
    forbidden_hits: int = 0,
):
    return {
        "model": model,
        "status": status,
        "elapsed_seconds": seconds,
        "score": {
            "score_100": score,
            "forbidden_hits": forbidden_hits,
        },
    }


class BenchmarkRecommendationTests(unittest.TestCase):
    def test_balanced_profile_prefers_observed_quality_over_raw_speed(self):
        rows = [
            result("small", 60, 10),
            result("small", 60, 10),
            result("large", 92, 20),
            result("large", 92, 20),
        ]
        recommendation = recommend_observed_model(rows, profile="balanced")
        self.assertEqual(recommendation.recommended_model, "large")
        self.assertGreater(
            recommendation.candidates[0].fit_score_100,
            recommendation.candidates[1].fit_score_100,
        )

    def test_unreliable_model_is_not_recommended(self):
        rows = [
            result("unstable", 95, 10),
            result("unstable", 0, 10, status="error"),
            result("stable", 75, 20),
            result("stable", 75, 20),
        ]
        recommendation = recommend_observed_model(rows)
        self.assertEqual(recommendation.recommended_model, "stable")
        unstable = next(
            item for item in recommendation.candidates if item.model == "unstable"
        )
        self.assertFalse(unstable.eligible)

    def test_no_successful_result_returns_no_recommendation(self):
        rows = [result("broken", 0, 1, status="error")]
        recommendation = recommend_observed_model(rows)
        self.assertEqual(recommendation.recommended_model, "")
        self.assertEqual(recommendation.candidates, ())
