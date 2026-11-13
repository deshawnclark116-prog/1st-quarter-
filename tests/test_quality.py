"""Deterministic guard, shadow-model and snapshot tests; no network calls."""
from __future__ import annotations
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from quarter_challenger import nba_starter_flags, role_challenger
from quarter_guard import confirmed_starters, gate_nba_predictions
from quarter_grade import archive_report, grade_snapshot, select_latest_pregame, summarize


class FirstQuarterQualityTests(unittest.TestCase):
    def setUp(self):
        self.game = {
            "id": "4", "sport": "nba", "name": "Away at Home",
            "date": (datetime.now(timezone.utc) + timedelta(minutes=40)).isoformat(),
            "date_et": "2026-11-13T19:00:00-05:00",
            "teams": [{"id": "1", "abbreviation": "BOS", "name": "Boston Celtics"},
                      {"id": "2", "abbreviation": "NY", "name": "New York Knicks"}],
        }
        self.pred = {"player": "BOS 0", "player_id": "a", "team_id": "1",
                     "team": "Boston Celtics", "metric": "points",
                     "estimate": 6.0, "low": 3, "high": 10,
                     "shadow_role_estimate": 7.0}

    def test_requires_five_individually_confirmed_players_on_both_teams(self):
        def team(code):
            return {"teamTricode": code, "players": [
                {"playerName": f"{code} {i}", "lineupStatus": "Confirmed"} for i in range(5)]}
        payload = {"games": [{"teams": [team("BOS"), team("NY")]}]}
        self.assertEqual(set(confirmed_starters(payload, self.game)), {"BOS", "NY"})
        payload["games"][0]["teams"][0]["players"][0]["lineupStatus"] = "Expected"
        self.assertIsNone(confirmed_starters(payload, self.game))

    def test_espn_nba_abbreviation_aliases_and_accents(self):
        from quarter_guard import normalized
        self.assertEqual(normalized("Nikola Vučević"), normalized("Nikola Vucevic"))
        game = dict(self.game, teams=[
            {"id": "1", "abbreviation": "NY", "name": "New York Knicks"},
            {"id": "2", "abbreviation": "GS", "name": "Golden State Warriors"}])
        def team(code):
            return {"teamTricode": code, "players": [
                {"playerName": f"{code} {i}", "lineupStatus": "Confirmed"} for i in range(5)]}
        payload = {"games": [{"teams": [team("NYK"), team("GSW")]}]}
        found = confirmed_starters(payload, game)
        self.assertEqual(set(found), {"NYK", "GSW"})

    def test_unverified_roster_never_publishes_official_nba(self):
        self.assertEqual(gate_nba_predictions(self.game, [self.pred], None, "url"), [])
        self.assertEqual(self.game["lineup_status"], "unconfirmed")
        accepted = gate_nba_predictions(self.game, [self.pred], {"BOS": {"bos0"}, "NY": set()}, "url")
        self.assertEqual(len(accepted), 1)
        self.assertTrue(accepted[0]["starter_confirmed"])

    def test_shadow_requires_three_starts_and_does_not_replace_baseline(self):
        self.assertIsNone(role_challenger([10, 0, 8], [True, False, True], 6.0))
        shadow = role_challenger([10, 0, 8, 7], [True, False, True, True], 6.0)
        self.assertIsInstance(shadow, float)
        self.assertGreater(shadow, 6.0)

    def test_archive_and_select_only_eligible_pregame(self):
        with tempfile.TemporaryDirectory() as root:
            good = dict(self.game, predictions=[self.pred], lineup_status="confirmed")
            self.assertEqual(archive_report({"games": [good]}, Path(root)), 1)
            self.assertEqual(len(select_latest_pregame(Path(root))), 1)
            late = dict(good, date=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
            self.assertEqual(archive_report({"games": [late]}, Path(root)), 0)

    def test_grade_requires_final_and_reconciled_q1_scores(self):
        snap = {"sport": "nba", "teams": [{"id": "1"}], "predictions": [self.pred],
                "date_et": "2026-11-13T19:00:00-05:00", "game_id": "4",
                "name": "test", "snapshot_at": "2026-11-13T18:00:00Z",
                "model_version": "q1_baseline_v1"}
        self.assertEqual(grade_snapshot(snap, {})[0], [])
        complete = {"header": {"competitions": [{
            "status": {"type": {"completed": True}},
            "competitors": [{"id": "1", "linescores": [{"value": 10}]}]}]},
            "plays": [{}]}
        with patch("quarter_grade.nba_stats", return_value=({"a": {"points": 10}}, {"a"})):
            rows, notes = grade_snapshot(snap, complete)
        self.assertFalse(notes)
        self.assertEqual(rows[0]["actual"], 10)
        self.assertEqual(rows[0]["absolute_error"], 4)
        self.assertEqual(rows[0]["shadow_absolute_error"], 3)
        self.assertEqual(summarize(rows)["shadow_comparison"][0]["mae_improvement"], 1.0)


if __name__ == "__main__":
    unittest.main()
