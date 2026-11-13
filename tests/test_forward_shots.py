import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from quarterbot import analyze_team
from quarter_grade import summarize
from quarter_shots import extract_q1_shots, fullgame_shots, forecast_shots


class ForwardShotShadowTests(unittest.TestCase):
    def test_shot_candidates_only_in_shadow_not_in_official_estimate(self):
        recent = [{"id":str(i),"date":"2026-11-01T00:00:00Z"} for i in range(4)]
        team = {"id":"1","name":"Test"}
        sample={"p":{"name":"Starter One","points":4}}
        shots={"p":{"2pa":3,"2pm":2,"3pa":2,"3pm":1,"fta":2,"ftm":2}}
        with (patch("quarterbot.recent_games",return_value=recent),
              patch("quarterbot.fetch_json",return_value={"plays":[],"boxscore":{}}),
              patch("quarterbot.nba_stats",return_value=(sample,{"p"})),
              patch("quarterbot.nba_starter_flags",return_value={"p":True}),
              patch("quarterbot.extract_q1_shots",return_value=shots),
              patch("quarterbot.fullgame_shots",return_value=shots)):
            preds=analyze_team("nba",team,datetime.now(timezone.utc),6,{},[])
        pts=[x for x in preds if x["metric"]=="points"]
        self.assertEqual(len(pts),1)
        self.assertEqual(pts[0]["estimate"],4.0)
        self.assertIn("shadow_shot_estimate",pts[0])
        self.assertGreater(pts[0]["shadow_shot_estimate"],0)

    def test_grade_summary_pairs_live_shot_and_baseline(self):
        rows=[{"sport":"nba","metric":"points","game_id":"4",
               "absolute_error":4.0,"error":4.0,"in_range":False,
               "shot_absolute_error":2.0,"shadow_absolute_error":None}]
        result=summarize(rows)
        self.assertEqual(result["shot_comparison"][0]["paired_n"],1)
        self.assertEqual(result["shot_comparison"][0]["mae_improvement"],2.0)
        self.assertFalse(result["shot_comparison"][0]["promoted"])


if __name__=="__main__":
    unittest.main()
