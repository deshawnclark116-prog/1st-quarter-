"""Daily, explainable first-quarter NBA and NFL player projections."""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


BASE = "https://site.api.espn.com/apis/site/v2/sports"
SPORTS = {"nba": "basketball/nba", "nfl": "football/nfl"}
METRICS = {
    "nba": ("points", "rebounds", "assists"),
    "nfl": ("passing_yards", "rushing_yards", "receiving_yards"),
}
MIN_DISPLAY = {
    "points": 1.5, "rebounds": 0.7, "assists": 0.5,
    "passing_yards": 20, "rushing_yards": 3, "receiving_yards": 3,
}


def fetch_json(url: str, params: dict | None = None, retries: int = 2) -> dict:
    if params:
        url += "?" + urlencode(params)
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urlopen(Request(url, headers={"User-Agent": "1st-quarter-bot/1.0"}), timeout=20) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def upcoming_games(sport: str, start: date, days: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    games = {}
    for offset in range(days):
        day = start + timedelta(days=offset)
        data = fetch_json(f"{BASE}/{SPORTS[sport]}/scoreboard", {"dates": day.strftime("%Y%m%d"), "limit": 100})
        for event in data.get("events", []):
            state = event.get("status", {}).get("type", {}).get("state")
            if state != "pre" or parse_time(event["date"]) <= now:
                continue
            teams = event.get("competitions", [{}])[0].get("competitors", [])
            if len(teams) != 2:
                continue
            games[event["id"]] = {
                "id": event["id"], "date": event["date"], "sport": sport,
                "name": event.get("name", "Game"),
                "teams": [{"id": t["team"]["id"], "name": t["team"]["displayName"],
                           "abbreviation": t["team"].get("abbreviation", "")}
                          for t in teams],
            }
    return sorted(games.values(), key=lambda game: game["date"])


def season_year(sport: str, game_date: datetime) -> int:
    if sport == "nba":
        return game_date.year + (game_date.month >= 7)
    return game_date.year - (game_date.month < 3)


def recent_games(sport: str, team_id: str, before: datetime, count: int) -> list[dict]:
    year = season_year(sport, before)
    data = fetch_json(f"{BASE}/{SPORTS[sport]}/teams/{team_id}/schedule", {"season": year})
    games = []
    for event in data.get("events", []):
        competition = event.get("competitions", [{}])[0]
        if (parse_time(event["date"]) < before and
                competition.get("status", {}).get("type", {}).get("completed") and
                competition.get("boxscoreAvailable", True)):
            games.append({"id": event["id"], "date": event["date"]})
    return sorted(games, key=lambda item: item["date"], reverse=True)[:count]


def nba_stats(summary: dict, team_id: str) -> tuple[dict, set]:
    names, roster = {}, set()
    for group in summary.get("boxscore", {}).get("players", []):
        if group.get("team", {}).get("id") != team_id:
            continue
        for athlete in group.get("statistics", [{}])[0].get("athletes", []):
            info = athlete.get("athlete", {})
            if info.get("id") and not athlete.get("didNotPlay"):
                names[info["id"]] = info.get("displayName", "Unknown")
                roster.add(info["id"])
    stats = defaultdict(lambda: defaultdict(float))
    for play in summary.get("plays", []):
        if play.get("period", {}).get("number") != 1 or play.get("team", {}).get("id") != team_id:
            continue
        participants = [p.get("athlete", {}).get("id") for p in play.get("participants", [])]
        kind = play.get("type", {}).get("text", "").lower()
        if participants and participants[0] in names:
            pid = participants[0]
            if play.get("scoringPlay"):
                stats[pid]["points"] += float(play.get("scoreValue") or 0)
            if "rebound" in kind:
                stats[pid]["rebounds"] += 1
        if len(participants) > 1 and "assists" in play.get("text", "").lower() and participants[1] in names:
            stats[participants[1]]["assists"] += 1
    return {pid: {"name": names[pid], **values} for pid, values in stats.items()}, roster


def nfl_aliases(summary: dict, team_id: str) -> tuple[dict, set]:
    aliases, roster = {}, set()
    for group in summary.get("boxscore", {}).get("players", []):
        if group.get("team", {}).get("id") != team_id:
            continue
        for category in group.get("statistics", []):
            if category.get("name") not in {"passing", "rushing", "receiving"}:
                continue
            for entry in category.get("athletes", []):
                info = entry.get("athlete", {})
                pid, first, last = info.get("id"), info.get("firstName", ""), info.get("lastName", "")
                if pid and first and last:
                    roster.add(pid)
                    # ESPN play text normally uses first initial and surname.
                    aliases[f"{first[0]}.{last.split()[0]}"] = (pid, info.get("displayName", first + " " + last))
    return aliases, roster


def nfl_stats(summary: dict, team_id: str) -> tuple[dict, set]:
    aliases, roster = nfl_aliases(summary, team_id)
    stats = defaultdict(lambda: defaultdict(float))
    for drive in summary.get("drives", {}).get("previous", []):
        if drive.get("team", {}).get("id") != team_id:
            continue
        for play in drive.get("plays", []):
            if play.get("period", {}).get("number") != 1:
                continue
            kind = play.get("type", {}).get("text", "").lower()
            if "penalty" in kind or "no play" in play.get("text", "").lower():
                continue
            description = play.get("text", "")
            if kind in {"rush", "rushing touchdown"}:
                match = re.search(r"\b([A-Z]\.[A-Za-z'-]+) (?:scrambles|[a-z]+|up the middle)", description)
                yards = re.search(r"for (-?\d+) yards?", description)
                if match and match.group(1) in aliases:
                    pid, name = aliases[match.group(1)]
                    stats[pid]["name"] = name
                    stats[pid]["rushing_yards"] += int(yards.group(1)) if yards else 0
            elif kind in {"pass reception", "passing touchdown"}:
                match = re.search(r"\b([A-Z]\.[A-Za-z'-]+) pass .*? to ([A-Z]\.[A-Za-z'-]+).*? for (-?\d+) yards?", description)
                if match:
                    yards = int(match.group(3))
                    for alias, metric in ((match.group(1), "passing_yards"), (match.group(2), "receiving_yards")):
                        if alias in aliases:
                            pid, name = aliases[alias]
                            stats[pid]["name"] = name
                            stats[pid][metric] += yards
    return {pid: dict(values) for pid, values in stats.items()}, roster


def project(values: list[float]) -> dict:
    # Recent games carry more weight; the range is empirical and widens for small samples.
    ordered = list(reversed(values))
    weights = list(range(1, len(ordered) + 1))
    mean = sum(v * w for v, w in zip(ordered, weights)) / sum(weights)
    spread = statistics.pstdev(values) if len(values) > 1 else max(1.0, mean * 0.6)
    spread = max(spread, mean * 0.25, 0.5)
    spread *= 1.2 if len(values) < 4 else 1
    return {"estimate": round(mean, 1), "low": round(max(0, mean - spread), 1),
            "high": round(mean + spread, 1), "games": len(values),
            "recent": [round(v, 1) for v in values]}


def analyze_team(sport: str, team: dict, before: datetime, sample_size: int,
                 cache: dict, errors: list[str]) -> list[dict]:
    games = recent_games(sport, team["id"], before, sample_size)
    if len(games) < 3:
        errors.append(f"{sport.upper()} {team['name']}: only {len(games)} completed games found")
        return []
    samples, latest_roster, names = defaultdict(list), set(), {}
    for index, game in enumerate(games):
        try:
            if game["id"] not in cache:
                cache[game["id"]] = fetch_json(f"{BASE}/{SPORTS[sport]}/summary", {"event": game["id"]})
            summary = cache[game["id"]]
            game_stats, roster = (nba_stats(summary, team["id"]) if sport == "nba"
                                  else nfl_stats(summary, team["id"]))
            if index == 0:
                latest_roster = roster
            for pid in roster:
                if pid in game_stats and "name" in game_stats[pid]:
                    names[pid] = game_stats[pid]["name"]
            # A rostered player gets zero for a metric with no first-quarter production.
            for pid in roster:
                for metric in METRICS[sport]:
                    samples[(pid, metric)].append(float(game_stats.get(pid, {}).get(metric, 0)))
        except (RuntimeError, KeyError, ValueError) as error:
            errors.append(f"{sport.upper()} game {game['id']}: {error}")
    results = []
    for (pid, metric), values in samples.items():
        if pid not in latest_roster or len(values) < 3 or pid not in names:
            continue
        prediction = project(values)
        if prediction["estimate"] >= MIN_DISPLAY[metric]:
            results.append({"player": names[pid], "player_id": pid, "team": team["name"],
                            "team_id": team["id"], "metric": metric, **prediction})
    return sorted(results, key=lambda result: (result["player"], result["metric"]))


def build_report(start: date, days: int, sample_size: int, sports: list[str]) -> dict:
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "start_date": start.isoformat(),
              "days": days, "sample_size": sample_size, "games": [], "warnings": []}
    cache, teams_cache = {}, {}
    for sport in sports:
        try:
            games = upcoming_games(sport, start, days)
        except RuntimeError as error:
            report["warnings"].append(str(error))
            continue
        for game in games:
            before = parse_time(game["date"])
            predictions = []
            for team in game["teams"]:
                key = (sport, team["id"], season_year(sport, before))
                if key not in teams_cache:
                    try:
                        teams_cache[key] = analyze_team(sport, team, before, sample_size, cache, report["warnings"])
                    except RuntimeError as error:
                        report["warnings"].append(f"{sport.upper()} {team['name']}: {error}")
                        teams_cache[key] = []
                predictions.extend(teams_cache[key])
            game["predictions"] = predictions
            report["games"].append(game)
    return report


def markdown(report: dict) -> str:
    lines = ["# First-quarter player projections", "", f"Generated: {report['generated_at']}",
             f"Window starts: {report['start_date']} ({report['days']} days); up to {report['sample_size']} prior games per team.", "",
             "Estimates use each player's recent first-quarter results, weighted toward newer games. "
             "Ranges show roughly one observed standard deviation around the estimate; they are not calibrated probabilities. "
             "Availability, injuries, lineup changes, and matchup adjustments are not verified. "
             "NFL yardage comes from play descriptions and may miss unusual or corrected plays.", ""]
    if not report["games"]:
        lines += ["No upcoming NBA or NFL games were found in the selected window.", ""]
    for game in report["games"]:
        start = parse_time(game["date"]).strftime("%Y-%m-%d %H:%M UTC")
        lines += [f"## {game['sport'].upper()}: {game['name']} — {start}", ""]
        if not game["predictions"]:
            lines += ["Insufficient usable first-quarter history for this game.", ""]
            continue
        lines += ["| Player | Team | First-quarter stat | Estimate | Range | Games | Recent games (newest first) |",
                  "|---|---|---|---:|---:|---:|---|"]
        for p in game["predictions"]:
            recent = ", ".join(str(v) for v in p["recent"])
            lines.append(f"| {p['player']} | {p['team']} | {p['metric'].replace('_', ' ')} | "
                         f"{p['estimate']} | {p['low']}–{p['high']} | {p['games']} | {recent} |")
        lines.append("")
    if report["warnings"]:
        lines += ["## Data warnings", ""] + [f"- {warning}" for warning in report["warnings"]] + [""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat,
                        default=datetime.now(ZoneInfo("America/New_York")).date())
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument("--games", type=int, default=6, help="prior games per team")
    parser.add_argument("--sport", choices=["nba", "nfl", "both"], default="both")
    parser.add_argument("--output", type=Path, default=Path("predictions.md"))
    args = parser.parse_args()
    if not 1 <= args.days <= 14 or not 3 <= args.games <= 20:
        parser.error("--days must be 1–14 and --games must be 3–20")
    sports = list(SPORTS) if args.sport == "both" else [args.sport]
    report = build_report(args.date, args.days, args.games, sports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown(report), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} and {args.output.with_suffix('.json')} ({len(report['games'])} games)")
    for warning in report["warnings"]:
        print("WARNING:", warning, file=sys.stderr)
    return 0 if report["games"] or not report["warnings"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
