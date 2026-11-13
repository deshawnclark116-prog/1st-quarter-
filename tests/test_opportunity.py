import unittest
from unittest.mock import patch

from quarter_opportunity import q1_minutes, weighted_median, candidate_forecasts


class OpportunityResearchTests(unittest.TestCase):
    def test_recency_weighted_median(self):
        self.assertEqual(weighted_median([8, 0, 0, 0]), 0.0)
        self.assertEqual(weighted_median([8, 8, 0, 0]), 8.0)

    def test_on_court_minutes_with_substitution(self):
        def play(minutes, seconds, ptype="", parts=None):
            return {"period": {"number": 1},
                    "clock": {"displayValue": f"{minutes}:{seconds:02}"},
                    "team": {"id": "1"},
                    "type": {"text": ptype},
                    "participants": ([{"athlete": {"id": str(p)}} for p in parts] if parts else [])}
        plays = [play(12, 0), play(6, 0, "Substitution", [6, 1]),
                 play(0, 0, "End Period")]
        with patch("quarter_challenger.nba_starter_flags",
                   return_value={str(x): True for x in range(1, 6)}):
            result = q1_minutes({"plays": plays}, "1")
        self.assertIsNotNone(result)
        self.assertEqual(result["1"], 6.0)
        self.assertEqual(result["6"], 6.0)
        for player in ("2", "3", "4", "5"):
            self.assertEqual(result[player], 12.0)
        self.assertAlmostEqual(sum(result.values()), 60.0)

    def test_missing_substitution_is_rejected(self):
        with patch("quarter_challenger.nba_starter_flags",
                   return_value={"1": True, "2": True}):
            self.assertIsNone(q1_minutes({"plays": []}, "1"))

    def test_candidates_not_using_target_outcome(self):
        samples = [
            {"value": v, "minutes": m, "starter": start}
            for v, m, start in [(9, 10, True), (7, 9, True),
                                 (5, 6, False), (6, 10, True),
                                 (8, 11, True), (0, 4, False)]
        ]
        result = candidate_forecasts(samples, "points", baseline=6.1,
                                     opponent_allowed=[30, 27, 29],
                                     current_team_scored=[26, 28, 25])
        self.assertIsNotNone(result["minute_rate"])
        self.assertIsNotNone(result["defense_shrink"])
        self.assertGreaterEqual(result["weighted_median"], 0)
        self.assertLessEqual(result["minute_rate"], 15)


if __name__ == "__main__":
    unittest.main()
