import unittest

from quarterbot import extract_official_inactives, filter_availability, markdown, nba_stats, nfl_stats, project, season_year
from datetime import datetime, timezone
from unittest.mock import patch


class ParserTests(unittest.TestCase):
    def test_nba_first_quarter_only_and_assist(self):
        summary = {
            "boxscore": {"players": [{"team": {"id": "1"}, "statistics": [{"athletes": [
                {"athlete": {"id": "a", "displayName": "Alex"}, "didNotPlay": False},
                {"athlete": {"id": "b", "displayName": "Blake"}, "didNotPlay": False},
            ]}]}]},
            "plays": [
                {"period": {"number": 1}, "team": {"id": "1"}, "type": {"text": "Jump Shot"},
                 "scoringPlay": True, "scoreValue": 2, "text": "Alex makes shot (Blake assists)",
                 "participants": [{"athlete": {"id": "a"}}, {"athlete": {"id": "b"}}]},
                {"period": {"number": 1}, "team": {"id": "1"}, "type": {"text": "Defensive Rebound"},
                 "participants": [{"athlete": {"id": "b"}}]},
                {"period": {"number": 2}, "team": {"id": "1"}, "type": {"text": "Jump Shot"},
                 "scoringPlay": True, "scoreValue": 3, "participants": [{"athlete": {"id": "a"}}]},
            ],
        }
        stats, roster = nba_stats(summary, "1")
        self.assertEqual(roster, {"a", "b"})
        self.assertEqual(stats["a"]["points"], 2)
        self.assertEqual(stats["b"]["rebounds"], 1)
        self.assertEqual(stats["b"]["assists"], 1)

    def test_nfl_first_quarter_yards_and_no_play(self):
        athletes = [
            {"athlete": {"id": "q", "firstName": "John", "lastName": "Jones", "displayName": "John Jones"}},
            {"athlete": {"id": "r", "firstName": "Bill", "lastName": "Reed", "displayName": "Bill Reed"}},
        ]
        summary = {
            "boxscore": {"players": [{"team": {"id": "1"}, "statistics": [
                {"name": "passing", "athletes": athletes[:1]},
                {"name": "rushing", "athletes": athletes[1:]},
                {"name": "receiving", "athletes": athletes[1:]},
            ]}]},
            "drives": {"previous": [{"team": {"id": "1"}, "plays": [
                {"period": {"number": 1}, "type": {"text": "Pass Reception"},
                 "text": "J.Jones pass short right to B.Reed for 13 yards."},
                {"period": {"number": 1}, "type": {"text": "Rush"},
                 "text": "B.Reed right tackle for 4 yards."},
                {"period": {"number": 1}, "type": {"text": "Penalty"},
                 "text": "B.Reed right tackle for 10 yards. No Play."},
                {"period": {"number": 2}, "type": {"text": "Rush"},
                 "text": "B.Reed right tackle for 20 yards."},
            ]}]},
        }
        stats, roster = nfl_stats(summary, "1")
        self.assertEqual(roster, {"q", "r"})
        self.assertEqual(stats["q"]["passing_yards"], 13)
        self.assertEqual(stats["r"]["receiving_yards"], 13)
        self.assertEqual(stats["r"]["rushing_yards"], 4)

    def test_projection_and_season(self):
        p = project([8, 6, 4, 2])
        self.assertGreater(p["estimate"], 5)
        self.assertLessEqual(p["low"], p["estimate"])
        self.assertEqual(season_year("nba", datetime(2026, 11, 13, tzinfo=timezone.utc)), 2027)
        self.assertEqual(season_year("nfl", datetime(2026, 1, 13, tzinfo=timezone.utc)), 2025)

    def test_evening_game_uses_eastern_calendar_date(self):
        report = {"generated_at": "2026-11-13T00:00:00+00:00", "start_date": "2026-11-13",
                  "days": 1, "sample_size": 6, "warnings": [], "games": [
                      {"sport": "nba", "name": "Away at Home", "date": "2026-11-14T00:00Z",
                       "predictions": []}]}
        self.assertIn("Fri Nov 13, 07:00 PM EST", markdown(report))
        self.assertIn("Generated: Thu Nov 12, 07:00 PM EST", markdown(report))

    def test_unavailable_player_and_backup_qb_are_removed(self):
        game = {"id": "1", "sport": "nfl", "name": "Away at Home", "teams": [
            {"id": "19", "name": "Home"}, {"id": "28", "name": "Away"}]}
        predictions = [
            {"player_id": "starter", "player": "Starter", "team_id": "19", "metric": "passing_yards"},
            {"player_id": "backup", "player": "Backup", "team_id": "19", "metric": "passing_yards"},
            {"player_id": "out", "player": "Out Player", "team_id": "28", "metric": "receiving_yards"},
            {"player_id": "q", "player": "Questionable", "team_id": "28", "metric": "receiving_yards"},
        ]
        preview = {"injuries": [{"injuries": [
            {"athlete": {"id": "out"}, "status": "Out"},
            {"athlete": {"id": "q"}, "status": "Questionable"}]}]}
        with (patch("quarterbot.fetch_json", return_value=preview),
              patch("quarterbot.active_roster", side_effect=lambda sport, team, before:
                    {"starter", "backup"} if team == "19" else {"out", "q"}),
              patch("quarterbot.official_nfl_inactives", return_value=({"19": ["QB Backup"], "28": []}, "https://amp.nfl.com/news/example")),
              patch("quarterbot.last_game_lead_qb", side_effect=lambda team, before, cache:
                    "starter" if team == "19" else None)):
            result = filter_availability(game, predictions,
                                         datetime(2026, 11, 13, tzinfo=timezone.utc), {}, {}, [])
        self.assertEqual({p["player"] for p in result}, {"Starter", "Questionable"})
        self.assertEqual(next(p for p in result if p["player"] == "Questionable")["availability"], "questionable")

    def test_official_inactive_article_requires_both_team_lists(self):
        teams = [{"id": "19", "name": "New York Giants"},
                 {"id": "28", "name": "Washington Commanders"}]
        article = """<h3>COMMANDERS</h3><ul><li>TE Colson Yankoff</li><li>QB Athan Kaliakmanis</li><li>C Lucas Patrick</li></ul>
                     <h3><strong>GIANTS</strong></h3><ul><li>LB Malik Harrison</li><li>CB Paulson Adebo</li><li>OL Josh Sills</li></ul>"""
        listed = extract_official_inactives(article, teams)
        self.assertIn("TE Colson Yankoff", listed["28"])
        self.assertIsNone(extract_official_inactives(article.split("<h3><strong>GIANTS")[0], teams))


if __name__ == "__main__":
    unittest.main()
