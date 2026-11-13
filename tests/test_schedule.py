"""Tests for kickoff-relative checks, T-15 retries and no-fake-refresh archival."""
from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from quarter_preflight import should_run
from quarter_schedule import game_phase, reusable_verified
from quarterbot import build_report
from quarter_grade import archive_report


def fake_game(start):
    return {
        "id":"12345", "sport":"nba", "name":"Away at Home",
        "date":start.isoformat(), "date_et":start.isoformat(),
        "teams":[{"id":"1","name":"Boston Celtics","abbreviation":"BOS"},
                 {"id":"2","name":"New York Knicks","abbreviation":"NY"}],
    }


PICK = {"player":"BOS 0","player_id":"a","team":"Boston Celtics",
        "team_id":"1","metric":"points","estimate":5.0,"low":2.0,
        "high":8.0,"games":10, "recent":[4,6,5]}


class GameWindowTests(unittest.TestCase):
    def setUp(self):
        self.tip=datetime(2026,11,13,23,0,tzinfo=timezone.utc)
        self.game=fake_game(self.tip)

    def test_phase_windows_are_relative_to_each_game(self):
        self.assertEqual(game_phase(self.game["date"],self.tip-timedelta(minutes=36)),"waiting")
        self.assertEqual(game_phase(self.game["date"],self.tip-timedelta(minutes=30)),"primary")
        self.assertEqual(game_phase(self.game["date"],self.tip-timedelta(minutes=21)),"primary")
        self.assertEqual(game_phase(self.game["date"],self.tip-timedelta(minutes=15)),"retry")
        self.assertEqual(game_phase(self.game["date"],self.tip-timedelta(minutes=3)),"retry")
        self.assertEqual(game_phase(self.game["date"],self.tip-timedelta(minutes=1)),"closed")
        self.assertEqual(game_phase(self.game["date"],self.tip+timedelta(minutes=4)),"closed")
        self.assertEqual(game_phase(self.game["date"],self.tip-timedelta(minutes=20)),"primary")

    def test_rejects_naive_timestamps_and_never_reuses_wrong_game(self):
        with self.assertRaises(ValueError):
            game_phase(self.game["date"],datetime(2026,11,13,22,30))
        valid=dict(self.game, predictions=[dict(PICK,starter_confirmed=True)],
                   lineup_status="confirmed",lineup_source="url")
        self.assertTrue(reusable_verified(self.game,valid))
        self.assertFalse(reusable_verified(dict(self.game,date=(self.tip+timedelta(minutes=10)).isoformat()),valid))
        self.assertFalse(reusable_verified(dict(self.game,id="different"),valid))
        self.assertFalse(reusable_verified(self.game,dict(valid,lineup_status="expected")))
        self.assertFalse(reusable_verified(self.game,dict(valid,predictions=[PICK])))

    def test_primary_unconfirmed_then_retry_verified(self):
        now=self.tip-timedelta(minutes=30)
        with (patch("quarterbot.upcoming_games",return_value=[copy.deepcopy(self.game)]),
              patch("quarterbot.analyze_team",return_value=[dict(PICK)]),
              patch("quarterbot.filter_availability",side_effect=lambda game,p,*args:p),
              patch("quarterbot.fetch_confirmed_starters",return_value=(None,"url"))):
            first=build_report(now.date(),1,6,["nba"],now=now)
        game=first["games"][0]
        self.assertEqual(game["run_phase"],"primary")
        self.assertEqual(game["lineup_status"],"unconfirmed")
        self.assertEqual(game["predictions"],[])
        retry_now=self.tip-timedelta(minutes=15)
        self.assertTrue(should_run(retry_now,[self.game],first)[0])
        with (patch("quarterbot.upcoming_games",return_value=[copy.deepcopy(self.game)]),
              patch("quarterbot.analyze_team",return_value=[dict(PICK)]),
              patch("quarterbot.filter_availability",side_effect=lambda game,p,*args:p),
              patch("quarterbot.fetch_confirmed_starters",
                    return_value=({"BOS":{"bos0"},"NYK":{"nyk0"}},"url"))):
            later=build_report(now.date(),1,6,["nba"],previous_report=first,now=retry_now)
        final=later["games"][0]
        self.assertEqual(final["run_phase"],"retry")
        self.assertEqual(final["lineup_status"],"confirmed")
        self.assertEqual(final["predictions"][0]["estimate"],5.0)
        self.assertTrue(final["predictions"][0]["starter_confirmed"])

    def test_primary_confirmed_retained_without_new_snapshot(self):
        at=self.tip-timedelta(minutes=30)
        with (patch("quarterbot.upcoming_games",return_value=[copy.deepcopy(self.game)]),
              patch("quarterbot.analyze_team",return_value=[dict(PICK)]),
              patch("quarterbot.filter_availability",side_effect=lambda game,p,*args:p),
              patch("quarterbot.fetch_confirmed_starters",
                    return_value=({"BOS":{"bos0"},"NYK":{"nyk0"}},"url"))):
            first=build_report(at.date(),1,6,["nba"],now=at)
        self.assertTrue(reusable_verified(self.game,first["games"][0]))
        at2=self.tip-timedelta(minutes=15)
        self.assertFalse(should_run(at2,[self.game],first)[0])
        with (patch("quarterbot.upcoming_games",return_value=[copy.deepcopy(self.game)]),
              patch("quarterbot.analyze_team",side_effect=AssertionError("must not recompute")),
              patch("quarterbot.fetch_confirmed_starters",side_effect=AssertionError("must not recheck"))):
            second=build_report(at2.date(),1,6,["nba"],previous_report=first,now=at2)
        self.assertEqual(second["games"][0]["run_phase"],"retained_verified_primary")
        self.assertTrue(second["games"][0]["reused_verified_snapshot"])
        self.assertEqual(second["games"][0]["predictions"],first["games"][0]["predictions"])
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(archive_report(second,Path(root)),0)

    def test_late_run_does_not_make_new_picks(self):
        now=self.tip-timedelta(minutes=1)
        with (patch("quarterbot.upcoming_games",return_value=[copy.deepcopy(self.game)]),
              patch("quarterbot.analyze_team",side_effect=AssertionError("late run forbidden"))):
            report=build_report(now.date(),1,6,["nba"],now=now)
        self.assertEqual(report["games"][0]["predictions"],[])
        self.assertEqual(report["games"][0]["run_phase"],"closed")

    def test_staggered_start_times_are_not_treated_as_one_batch(self):
        now=self.tip-timedelta(minutes=30)
        later=fake_game(self.tip+timedelta(hours=2))
        later["id"]="later"
        with (patch("quarterbot.upcoming_games",return_value=[copy.deepcopy(self.game),later]),
              patch("quarterbot.analyze_team",return_value=[]),
              patch("quarterbot.filter_availability",return_value=[]),
              patch("quarterbot.fetch_confirmed_starters",return_value=(None,"url"))):
            report=build_report(now.date(),1,6,["nba"],now=now)
        self.assertEqual([g["run_phase"] for g in report["games"]],["primary","waiting"])
        self.assertEqual(report["games"][1]["predictions"],[])


if __name__=="__main__":
    unittest.main()
