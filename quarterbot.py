"""Daily, explainable first-quarter NBA and NFL player projections."""

from __future__ import annotations

import argparse
import html
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
from quarter_guard import fetch_confirmed_starters, gate_nba_predictions
from quarter_challenger import nba_starter_flags, role_challenger
from quarter_shots import extract_q1_shots, fullgame_shots, forecast_shots
from quarter_schedule import game_phase, index_previous, reusable_verified


BASE = "https://site.api.espn.com/apis/site/v2/sports"
LOCAL_TZ = ZoneInfo("America/New_York")
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
            with urlopen(Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://www.espn.com/",
            }), timeout=20) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Could not fetch {url}: {last_error}")


def fetch_text(url: str) -> str:
    try:
        with urlopen(Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
                "Accept": "application/json,text/plain,*/*",
                "Referer": "https://www.espn.com/",
            }), timeout=20) as response:
            return response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError) as error:
        raise RuntimeError(f"Could not fetch {url}: {error}") from error


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
                "id": event["id"], "date": event["date"],
                "date_et": parse_time(event["date"]).astimezone(LOCAL_TZ).isoformat(), "sport": sport,
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
    role_samples = defaultdict(list)
    shot_history = defaultdict(list)
    for index, game in enumerate(games):
        try:
            if game["id"] not in cache:
                cache[game["id"]] = fetch_json(f"{BASE}/{SPORTS[sport]}/summary", {"event": game["id"]})
            summary = cache[game["id"]]
            game_stats, roster = (nba_stats(summary, team["id"]) if sport == "nba"
                                  else nfl_stats(summary, team["id"]))
            known_starters = nba_starter_flags(summary, team["id"]) if sport == "nba" else {}
            shot_q1 = extract_q1_shots(summary, team["id"]) if sport == "nba" else {}
            shot_full = fullgame_shots(summary, team["id"]) if sport == "nba" else {}
            if index == 0:
                latest_roster = roster
            for pid in roster:
                if pid in game_stats and "name" in game_stats[pid]:
                    names[pid] = game_stats[pid]["name"]
            # A rostered player gets zero for a metric with no first-quarter production.
            for pid in roster:
                for metric in METRICS[sport]:
                    samples[(pid, metric)].append(float(game_stats.get(pid, {}).get(metric, 0)))
                    role_samples[(pid, metric)].append(known_starters.get(str(pid)))
                if sport == "nba":
                    shot_history[pid].append({
                        "q1_shots": shot_q1.get(str(pid), {}),
                        "full_shots": shot_full.get(str(pid), {}),
                        "starter": known_starters.get(str(pid)), "minutes": None,
                    })
        except (RuntimeError, KeyError, ValueError) as error:
            errors.append(f"{sport.upper()} game {game['id']}: {error}")
    results = []
    for (pid, metric), values in samples.items():
        if pid not in latest_roster or len(values) < 3 or pid not in names:
            continue
        prediction = project(values)
        if prediction["estimate"] >= MIN_DISPLAY[metric]:
            # An independent, role-aware challenger is logged but never promoted
            # until future-only paired grading proves superior.
            if sport == "nba":
                shadow = role_challenger(values, role_samples[(pid, metric)], prediction["estimate"])
                if shadow is not None:
                    prediction["shadow_role_estimate"] = shadow
                if metric == "points" and len(shot_history.get(pid, [])) >= 3:
                    # Research-only: this never overrides the official estimate.
                    experimental = forecast_shots(shot_history[pid], prediction["estimate"])
                    if experimental.get("shot_volume") is not None:
                        prediction["shadow_shot_estimate"] = experimental["shot_volume"]
            results.append({"player": names[pid], "player_id": pid, "team": team["name"],
                            "team_id": team["id"], "metric": metric, **prediction})
    return sorted(results, key=lambda result: (result["player"], result["metric"]))


def active_roster(sport: str, team_id: str, before: datetime) -> set[str]:
    data = fetch_json(f"{BASE}/{SPORTS[sport]}/teams/{team_id}/roster",
                      {"season": season_year(sport, before)})
    groups = data.get("athletes", [])
    if sport == "nba":
        athletes = groups
    else:
        athletes = [athlete for group in groups
                    if group.get("position") in {"offense", "defense", "specialTeam"}
                    for athlete in group.get("items", [])]
    if not athletes:
        raise RuntimeError(f"No current roster for team {team_id}")
    return {athlete["id"] for athlete in athletes
            if athlete.get("id") and athlete.get("status", {}).get("type") == "active"}


def last_game_lead_qb(team_id: str, before: datetime, cache: dict) -> str | None:
    games = recent_games("nfl", team_id, before, 1)
    if not games:
        return None
    game_id = games[0]["id"]
    if game_id not in cache:
        cache[game_id] = fetch_json(f"{BASE}/{SPORTS['nfl']}/summary", {"event": game_id})
    candidates = []
    for group in cache[game_id].get("boxscore", {}).get("players", []):
        if group.get("team", {}).get("id") != team_id:
            continue
        for category in group.get("statistics", []):
            if category.get("name") != "passing":
                continue
            for entry in category.get("athletes", []):
                try:
                    attempts = int(entry["stats"][0].split("/")[-1])
                    candidates.append((attempts, entry["athlete"]["id"]))
                except (IndexError, KeyError, ValueError):
                    continue
    return max(candidates)[1] if candidates and max(candidates)[0] > 0 else None


def normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def extract_official_inactives(page: str, teams: list[dict]) -> dict[str, list[str]] | None:
    """Read the team sections of an NFL.com game-day inactive article."""
    headings = {}
    for match in re.finditer(r"<h3\b[^>]*>(.*?)</h3>(.*?)(?=<h3\b|$)", page, re.I | re.S):
        title = html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip().upper()
        listed = re.search(r"<ul\b[^>]*>(.*?)</ul>", match.group(2), re.I | re.S)
        if not listed:
            continue
        players = [" ".join(html.unescape(re.sub(r"<[^>]+>", "", item)).split())
                   for item in re.findall(r"<li\b[^>]*>(.*?)</li>", listed.group(1), re.I | re.S)]
        if players:
            headings[title] = players
    found = {}
    for team in teams:
        title = team["name"].split()[-1].upper()
        if title not in headings or len(headings[title]) < 3:
            return None
        found[team["id"]] = headings[title]
    return found


def official_nfl_inactives(game: dict, preview: dict) -> tuple[dict[str, list[str]], str] | None:
    week = preview.get("header", {}).get("week")
    if not isinstance(week, int):
        return None
    local = parse_time(game["date"]).astimezone(LOCAL_TZ)
    season = season_year("nfl", local)
    day = local.strftime("%A").lower()
    matchup = re.sub(r"[^a-z0-9]+", "-", game["name"].lower()).strip("-")
    if day in {"thursday", "monday"}:
        slug = f"week-{week}-{day}-night-inactives-{matchup}"
    elif day == "sunday":
        slug = f"nfl-week-{week}-inactives-players-ruled-out-sunday-{season}-season"
    else:
        return None
    url = f"https://amp.nfl.com/news/{slug}"
    try:
        listed = extract_official_inactives(fetch_text(url), game["teams"])
    except RuntimeError:
        return None
    return (listed, url) if listed else None


def filter_availability(game: dict, predictions: list[dict], before: datetime,
                        roster_cache: dict, summary_cache: dict, errors: list[str]) -> list[dict]:
    sport = game["sport"]
    try:
        preview = fetch_json(f"{BASE}/{SPORTS[sport]}/summary", {"event": game["id"]})
        if "injuries" not in preview:
            raise RuntimeError("Pregame injury data unavailable")
        injuries = {entry.get("athlete", {}).get("id"): entry.get("status", "").lower()
                    for group in preview["injuries"] for entry in group.get("injuries", [])}
        active = {}
        for team in game["teams"]:
            key = (sport, team["id"], season_year(sport, before))
            if key not in roster_cache:
                roster_cache[key] = active_roster(sport, team["id"], before)
            active[team["id"]] = roster_cache[key]
        lead_qbs = {}
        official = None
        if sport == "nfl":
            official = official_nfl_inactives(game, preview)
            if official is None:
                game["withheld_reason"] = "NFL.com game-day inactive list is not yet available or could not be verified."
                return []
            game["availability_source"] = official[1]
            for team in game["teams"]:
                lead_qbs[team["id"]] = last_game_lead_qb(team["id"], before, summary_cache)
    except (RuntimeError, KeyError, ValueError) as error:
        errors.append(f"{sport.upper()} {game['name']}: availability could not be checked: {error}")
        game["withheld_reason"] = "Roster or injury check failed."
        return []
    eligible = []
    excluded = []
    for prediction in predictions:
        pid, team_id = prediction["player_id"], prediction["team_id"]
        status = injuries.get(pid, "")
        if pid not in active[team_id]:
            excluded.append(prediction["player"] + " (not on active roster)")
        elif official and any(normalized_name(prediction["player"]) in normalized_name(item)
                              for item in official[0][team_id]):
            excluded.append(prediction["player"] + " (officially inactive)")
        elif status in {"out", "doubtful", "inactive", "suspended"}:
            excluded.append(prediction["player"] + f" ({status})")
        elif sport == "nfl" and prediction["metric"] == "passing_yards" and pid != lead_qbs[team_id]:
            excluded.append(prediction["player"] + " (not last game's lead passer)")
        else:
            prediction["availability"] = "questionable" if status == "questionable" else "active roster; game status unconfirmed"
            eligible.append(prediction)
    game["excluded"] = sorted(set(excluded))
    return eligible


def build_report(start: date, days: int, sample_size: int, sports: list[str],
                 nba_sample_size: int = 10, previous_report: dict | None = None,
                 now: datetime | None = None) -> dict:
    # NBA ten-game horizon beat six on 2025/2026 date-separated paired tests.
    # NFL stays on the separately unvalidated six-game horizon.
    reference_now = now or datetime.now(timezone.utc)
    report = {"generated_at": reference_now.isoformat(), "start_date": start.isoformat(),
              "days": days, "sample_size": sample_size,
              "nba_sample_size": nba_sample_size, "games": [], "warnings": [],
              "publication_policy": "scheduled_game_minus_30_with_unconfirmed_retry_minus_15"}
    cached_previous = index_previous(previous_report)
    cache, teams_cache, roster_cache = {}, {}, {}
    for sport in sports:
        try:
            games = upcoming_games(sport, start, days)
        except RuntimeError as error:
            report["warnings"].append(str(error))
            continue
        for game in games:
            before = parse_time(game["date"])
            phase = game_phase(game["date"], reference_now)
            game["run_phase"] = phase
            if phase == "waiting":
                game["predictions"] = []
                game["withheld_reason"] = "Awaiting first lineup check approximately 30 minutes before scheduled tipoff."
                report["games"].append(game)
                continue
            if phase == "closed":
                game["predictions"] = []
                game["withheld_reason"] = "Pregame cutoff reached; no new predictions published inside two minutes of tipoff."
                report["games"].append(game)
                continue
            previous = cached_previous.get((sport, str(game["id"])))
            if reusable_verified(game, previous):
                # If T-30 starters/inactives were already verified, do not rerun at
                # T-15 and do not forge a new prediction timestamp or archive.
                preserved = dict(previous)
                preserved["run_phase"] = "retained_verified_primary"
                preserved["reused_verified_snapshot"] = True
                report["games"].append(preserved)
                continue
            game["checked_at"] = reference_now.isoformat()
            predictions = []
            for team in game["teams"]:
                key = (sport, team["id"], season_year(sport, before))
                if key not in teams_cache:
                    try:
                        teams_cache[key] = analyze_team(sport, team, before,
                                                        nba_sample_size if sport == "nba" else sample_size,
                                                        cache, report["warnings"])
                    except RuntimeError as error:
                        report["warnings"].append(f"{sport.upper()} {team['name']}: {error}")
                        teams_cache[key] = []
                predictions.extend(teams_cache[key])
            game["predictions"] = filter_availability(game, predictions, before,
                                                      roster_cache, cache, report["warnings"])
            if sport == "nba":
                # A listed active roster is NOT a confirmed starting five.
                starters, source = fetch_confirmed_starters(game)
                game["predictions"] = gate_nba_predictions(
                    game, game["predictions"], starters, source
                )
            # A delayed Actions job can cross the tipoff cutoff while fetching
            # historical games. In that case, fail closed even if lineups were valid.
            if (now or datetime.now(timezone.utc)) >= before - timedelta(minutes=2):
                game["predictions"] = []
                game["withheld_reason"] = "Pregame cutoff reached before publication; projections withheld."
            report["games"].append(game)
    return report


def markdown(report: dict) -> str:
    generated = parse_time(report["generated_at"]).astimezone(LOCAL_TZ).strftime("%a %b %d, %I:%M %p %Z")
    lines = ["# Experimental first-quarter baseline", "", f"Generated: {generated}",
             f"Window starts: {report['start_date']} Eastern ({report['days']} days); NBA up to {report.get('nba_sample_size', report['sample_size'])} and NFL up to {report['sample_size']} prior games per team.",
             "Game times are shown in Eastern time (EST or EDT). Games already started when the report runs are excluded.", "",
             "Current team rosters and listed Out/Doubtful injuries are checked before a player is shown. "
             "NFL projections require NFL.com's published game-day inactive list; they are withheld until it can be verified. "
             "NFL passing estimates show the team's most recent lead passer. Tonight's starter and participation are not confirmed. "
             "Estimates use each player's recent first-quarter results, weighted toward newer games. "
             "Ranges show roughly one observed standard deviation around the estimate; they are not calibrated probabilities. "
             "Late lineup changes and matchup adjustments are not reflected. "
             "NFL yardage comes from play descriptions and may miss unusual or corrected plays. "
             "This baseline has not yet shown predictive improvement in a backtest.", ""]
    if not report["games"]:
        lines += ["No upcoming NBA or NFL games were found in the selected window.", ""]
    for game in report["games"]:
        start = parse_time(game["date"]).astimezone(LOCAL_TZ).strftime("%a %b %d, %I:%M %p %Z")
        lines += [f"## {game['sport'].upper()}: {game['name']} — {start}", ""]
        if not game["predictions"]:
            lines += [game.get("withheld_reason", "Insufficient usable first-quarter history for this game."), ""]
            continue
        if game.get("availability_source"):
            lines += [f"Official inactives: [NFL.com]({game['availability_source']}).", ""]
        if game.get("excluded"):
            lines += ["Excluded after roster/injury/lead-passer check: " + ", ".join(game["excluded"]) + ".", ""]
        lines += ["| Player | Team | First-quarter stat | Estimate | Range | Games | Recent games (newest first) |",
                  "|---|---|---|---:|---:|---:|---|"]
        for p in game["predictions"]:
            recent = ", ".join(str(v) for v in p["recent"])
            player = p["player"] + (" (questionable)" if p.get("availability") == "questionable" else "")
            lines.append(f"| {player} | {p['team']} | {p['metric'].replace('_', ' ')} | "
                         f"{p['estimate']} | {p['low']}–{p['high']} | {p['games']} | {recent} |")
        lines.append("")
    if report["warnings"]:
        lines += ["## Data warnings", ""] + [f"- {warning}" for warning in report["warnings"]] + [""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat,
                        default=datetime.now(LOCAL_TZ).date())
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument("--games", type=int, default=6, help="prior NFL games per team")
    parser.add_argument("--nba-games", type=int, default=10, help="prior NBA games per team (validated 10 by default)")
    parser.add_argument("--sport", choices=["nba", "nfl", "both"], default="both")
    parser.add_argument("--output", type=Path, default=Path("predictions.md"))
    args = parser.parse_args()
    if not 1 <= args.days <= 14 or not 3 <= args.games <= 20 or not 3 <= args.nba_games <= 20:
        parser.error("--days must be 1–14 and game-history windows must be 3–20")
    sports = list(SPORTS) if args.sport == "both" else [args.sport]
    previous_report = None
    previous_path = args.output.with_suffix(".json")
    if previous_path.exists():
        try:
            previous_report = json.loads(previous_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print("WARNING: unreadable previous report; no cached picks will be reused.", file=sys.stderr)
    report = build_report(args.date, args.days, args.games, sports,
                          nba_sample_size=args.nba_games, previous_report=previous_report)
    # Model outputs remain experimental; this is a verified-starter gate, not
    # an assertion of calibrated betting probabilities.
    from quarter_grade import archive_report
    archived = archive_report(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(markdown(report), encoding="utf-8")
    args.output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} and {args.output.with_suffix('.json')} ({len(report['games'])} games); archived {archived} pregame snapshots")
    for warning in report["warnings"]:
        print("WARNING:", warning, file=sys.stderr)
    return 0 if report["games"] or not report["warnings"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
