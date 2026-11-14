import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from preview_backtest import grade_game, completed_q1, find_event, paired_models, means


class Nov13PreviewBacktestTests(unittest.TestCase):
    def setUp(self):
        self.tip = datetime(2026,11,14,0,0,tzinfo=timezone.utc)
        self.created = self.tip - timedelta(minutes=30)
        self.event = {
            "id":"401000001", "date":self.tip.isoformat(),
            "name":"Away at Home","status":{"period":2,"type":{"completed":False}},
            "competitions":[{"competitors":[
                {"team":{"displayName":"Home Team"}},
                {"team":{"displayName":"Away Team"}},
            ]}],
        }
        self.preview={
            "game":"Away Team at Home Team",
            "date_et":"2026-11-13T19:00:00-05:00",
            "confirmed_starters_both_teams":False,
            "roster_injuries_verified":True,
            "projections":[
                {"player":"A Player","player_id":"a","team_id":"1","team":"Home Team",
                 "metric":"points","estimate":5.0,"low":2,"high":8,
                 "shadow_shot_estimate":4.0, "shadow_role_estimate":5.5},
                {"player":"B Player","player_id":"b","team_id":"2","team":"Away Team",
                 "metric":"points","estimate":3.0,"low":1,"high":6,
                 "shadow_shot_estimate":2.2},
                {"player":"B Player","player_id":"b","team_id":"2","team":"Away Team",
                 "metric":"rebounds","estimate":2.1,"low":1,"high":4},
            ]}
        self.summary={"plays":[{"period":{"number":1}},{"period":{"number":2}}],
            "header":{"competitions":[{"competitors":[
                {"id":"1","linescores":[{"value":8}]},
                {"id":"2","linescores":[{"value":7}]}
            ]}]}}
        self.output={
            "1":({"a":{"name":"A Player","points":8.0} },{"a"}),
            "2":({"b":{"name":"B Player","points":7.0,"rebounds":2}},{"b"})
        }

    def test_find_unique_game_by_actual_team_names(self):
        self.assertEqual(find_event(self.preview,[self.event])["id"],"401000001")
        self.assertIsNone(find_event(self.preview,[]))
        self.assertIsNone(find_event(self.preview,[self.event,self.event]))

    def test_q1_complete_status_or_second_period(self):
        self.assertTrue(completed_q1(self.summary,self.event))
        event=dict(self.event,status={"period":1,"type":{"completed":False}})
        self.assertTrue(completed_q1(self.summary,event))
        self.assertFalse(completed_q1({"plays":[{"period":{"number":1}}]},event))

    def test_grade_actual_rows_and_paired_models(self):
        with patch("preview_backtest.nba_stats",side_effect=lambda summary,tid:self.output[tid]):
            rows,report=grade_game(self.preview,self.created,self.event,self.summary)
        self.assertEqual(report["status"],"graded")
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]["actual"],8.0)
        self.assertEqual(rows[0]["shadow_shot_ae"],4.0)
        self.assertEqual(rows[2]["actual"],2.0)
        self.assertEqual(report["by_market"]["points"]["n"],2)
        self.assertEqual(report["shot_model_comparison"]["n"],2)
        self.assertEqual(report["pre_tipoff_minutes"],30)

    def test_unverified_participation_not_assumed_zero(self):
        data=dict(self.preview)
        data["projections"]=list(self.preview["projections"])+[
            dict(self.preview["projections"][0],player="Missing",player_id="unknown")]
        with patch("preview_backtest.nba_stats",side_effect=lambda summary,tid:self.output[tid]):
            rows,report=grade_game(data,self.created,self.event,self.summary)
        self.assertEqual(len(rows),3)
        self.assertEqual(report["excluded_unverified_participation"],1)

    def test_nonreconciled_points_blocks_every_market(self):
        summary={"header":{"competitions":[{"competitors":[
            {"id":"1","linescores":[{"value":9}]},
            {"id":"2","linescores":[{"value":7}]}
        ]}]},"plays":[{"period":{"number":2}}]}
        with patch("preview_backtest.nba_stats",side_effect=lambda summary,tid:self.output[tid]):
            rows,report=grade_game(self.preview,self.created,self.event,summary)
        self.assertEqual(rows,[])
        self.assertEqual(report["status"],"excluded_q1_pbp_mismatch")

    def test_no_future_or_tipoff_snapshot(self):
        with patch("preview_backtest.nba_stats",side_effect=AssertionError("must not fetch stats")):
            rows,report=grade_game(self.preview,self.tip,self.event,self.summary)
        self.assertEqual(rows,[])
        self.assertEqual(report["status"],"excluded_late_snapshot")

    def test_shot_model_metrics_paired_only(self):
        sample=[
            {"metric":"points","absolute_error":2,"shadow_shot_ae":1,"error":2,
             "estimate":3,"actual":1,"within_range":True},
            {"metric":"assists","absolute_error":1,"shadow_shot_ae":None,"error":-1,
             "estimate":1,"actual":2,"within_range":False},
        ]
        result=paired_models(sample)
        self.assertEqual(result["shot_improvement_pct"],50)
        self.assertEqual(means(sample,"assists")["n"],1)


if __name__=="__main__":
    unittest.main()
