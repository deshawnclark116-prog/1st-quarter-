import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from quarterbot import build_report, markdown


class SportHistoryWindowTests(unittest.TestCase):
    def test_nba_10_games_nfl_6_and_report_discloses_both(self):
        date=(datetime.now(timezone.utc)+timedelta(minutes=60)).isoformat()
        def event(sport):
            return {"id":sport,"date":date,"date_et":"2026-11-13T19:00:00-05:00",
                    "sport":sport,"name":"Test vs Test",
                    "teams":[{"id":"1","name":"Home","abbreviation":"BOS"},
                             {"id":"2","name":"Away","abbreviation":"NY"}]}
        lengths=[]
        def analyze(sport,team,before,size,cache,errors):
            lengths.append((sport,size))
            return []
        with (patch("quarterbot.upcoming_games",side_effect=lambda sport,start,days:[event(sport)]),
              patch("quarterbot.analyze_team",side_effect=analyze),
              patch("quarterbot.filter_availability",return_value=[]),
              patch("quarterbot.fetch_confirmed_starters",return_value=(None,"https://example.test"))):
            report=build_report(datetime.now(timezone.utc).date(),1,6,["nba","nfl"],nba_sample_size=10)
        self.assertEqual(lengths.count(("nba",10)),2)
        self.assertEqual(lengths.count(("nfl",6)),2)
        self.assertIn("NBA up to 10 and NFL up to 6",markdown(report))


if __name__=="__main__":
    unittest.main()
