import unittest
from quarter_validation import validate_shot_shadow


class ForwardValidationTests(unittest.TestCase):
    def test_no_samples_never_promotes(self):
        data=validate_shot_shadow([])
        self.assertFalse(data["accuracy_gate_passed"])
        self.assertFalse(data["profitable_betting_proven"])

    def test_big_accuracy_gain_still_not_a_betting_win(self):
        rows=[]
        for d in range(20):
            for game in range(3):
                for player in range(10):
                    rows.append({"date_et":f"2026-11-{d+1:02d}",
                                 "game_id":f"{d}-{game}","sport":"nba",
                                 "metric":"points","absolute_error":2.0,
                                 "shot_absolute_error":1.5})
        data=validate_shot_shadow(rows,bootstrap_n=300)
        self.assertTrue(data["accuracy_gate_passed"])
        self.assertFalse(data["profitable_betting_proven"])
        self.assertFalse(data["promotion_automatic"])

    def test_unpaired_or_wrong_market_does_not_count(self):
        rows=[{"date_et":"2026-11-13","game_id":"1","sport":"nba",
               "metric":"assists","absolute_error":1,
               "shot_absolute_error":0},
              {"date_et":"2026-11-13","game_id":"1","sport":"nba",
               "metric":"points","absolute_error":2,
               "shot_absolute_error":None}]
        data=validate_shot_shadow(rows)
        self.assertEqual(data["paired_n"],0)


if __name__=="__main__":
    unittest.main()
