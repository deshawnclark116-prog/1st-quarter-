"""One-time EARLY, UNVERIFIED 8 PM Q1 model preview, not official picks."""
import json
from datetime import date, datetime, timezone
from pathlib import Path
from quarterbot import upcoming_games, analyze_team, filter_availability, parse_time, LOCAL_TZ
from quarter_guard import fetch_confirmed_starters, normalized

def main():
    d=date(2026,11,13)
    games=[g for g in upcoming_games("nba",d,1)
           if parse_time(g["date"]).astimezone(LOCAL_TZ).strftime("%H:%M")=="20:00"]
    cache,rosters,errors={}, {}, []
    output={"created_at_utc":datetime.now(timezone.utc).isoformat(),
            "status":"EARLY UNVERIFIED MODEL PREVIEW - NOT OFFICIAL PICKS",
            "nba_history_games":10,
            "note":"Game starters are not assumed confirmed. Model is experimental; ranges are not calibrated win probabilities.",
            "games":[],"warnings":errors}
    for game in games:
        raw=[]
        for team in game["teams"]:
            try:
                raw.extend(analyze_team("nba",team,parse_time(game["date"]),10,cache,errors))
            except Exception as exc:
                errors.append(team["name"]+": "+repr(exc))
        verify,src=fetch_confirmed_starters(game)
        filtered=filter_availability(game,raw,parse_time(game["date"]),rosters,cache,errors)
        # Use filtered data when the injury/roster validation completes.
        checked=(not bool(game.get("withheld_reason")))
        projections=(filtered if checked else raw)
        if verify:
            confirmed_codes=set()
            for team in game["teams"]:
                from quarter_guard import _team_code
                code=_team_code(team["abbreviation"])
                confirmed_codes.update((team["name"],name) for name in verify.get(code,set()))
            if checked:
                projections=[p for p in projections if (p["team"],normalized(p["player"])) in confirmed_codes]
        entry={"game":game["name"],"date_et":game["date_et"],
               "confirmed_starters_both_teams":bool(verify),
               "lineup_source":src,
               "roster_injuries_verified":checked,
               "availability_warning":game.get("withheld_reason"),
               "excluded":game.get("excluded",[]),
               "total_preview_projections":len(projections),
               "projections":projections}
        output["games"].append(entry)
    Path("early_preview_8pm.json").write_text(json.dumps(output,indent=2)+"\n",encoding="utf-8")
    for game in output["games"]:
        print("MATCHUP",game["game"],"verified",game["confirmed_starters_both_teams"],
              "roster_checked",game["roster_injuries_verified"],"n",game["total_preview_projections"])
        for market in ("points","rebounds","assists"):
            entries=sorted((p for p in game["projections"] if p["metric"]==market),
                           key=lambda p:p["estimate"],reverse=True)[:10]
            print("MARKET",market)
            for p in entries:
                print(p["player"],p["team"],p["estimate"],"range",p["low"],p["high"],
                      "games",p["games"],"shadow_shot",p.get("shadow_shot_estimate"))
    print("WARNINGS",errors)
if __name__=="__main__":
    main()
