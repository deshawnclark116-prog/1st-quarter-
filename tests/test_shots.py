import unittest
from quarter_shots import extract_q1_shots, fullgame_shots, forecast_shots


class ShotModelTests(unittest.TestCase):
    def test_q1_attempts_include_misses_but_no_second_quarter(self):
        def p(attempted, scored, period=1):
            return {"period": {"number": period}, "team": {"id": "1"},
                    "shootingPlay": True, "scoringPlay": scored,
                    "pointsAttempted": attempted,
                    "participants": [{"athlete":{"id": "a"}}]}
        s = {"plays": [p(2,False),p(2,True),p(3,True),p(3,False),
                       p(1,False),p(1,True),p(3,True,2)]}
        result = extract_q1_shots(s, "1")
        self.assertEqual(result["a"], {"2pa":2.0,"2pm":1.0,
                                       "3pa":2.0,"3pm":1.0,
                                       "fta":2.0,"ftm":1.0})

    def test_fullgame_boxscore_two_point_subtraction(self):
        s = {"boxscore":{"players":[{"team":{"id":"1"},
             "statistics":[{"athletes":[{"athlete":{"id":"a"},
                  "stats":["30","20","7-15","3-8","3-3"]}]}]}]}}
        output=fullgame_shots(s,"1")["a"]
        self.assertEqual(output["2pa"],7)
        self.assertEqual(output["2pm"],4)
        self.assertEqual(output["3pa"],8)
        self.assertEqual(output["ftm"],3)

    def test_opportunity_model_independent_of_future_result(self):
        samples=[{"q1_shots":{"2pa":3,"3pa":2,"fta":2},
                  "full_shots":{"2pa":6,"2pm":3,"3pa":5,"3pm":2,"fta":4,"ftm":3},
                  "starter":True, "minutes":9} for _ in range(6)]
        result=forecast_shots(samples, baseline=6, projected_minutes=9)
        self.assertGreater(result["shot_volume"],0)
        self.assertGreater(result["shot_starter"],0)
        self.assertGreater(result["shot_minutes"],0)
        self.assertAlmostEqual(result["shot_half_shrink"],(result["shot_volume"]+6)/2,places=1)


if __name__=="__main__":
    unittest.main()
