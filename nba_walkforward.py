"""Chronological NBA Q1 backtest, using only prior games for each estimate.

Important limitation: target-game starters are sourced from the FINAL boxscore
because historical pre-tipoff confirmation timestamps were not archived.
Thus comparisons are ORACLE-STARTERS diagnostics, NOT verified pregame ROI.
No model is promoted automatically.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from quarterbot import BASE, SPORTS, MIN_DISPLAY, LOCAL_TZ, fetch_json, nba_stats, parse_time, project
from quarter_challenger import nba_starter_flags, role_challenger
from quarter_grade import _q1_team_score


def dates_between(begin: date, finish: date):
    day = begin
    while day <= finish:
        yield day
        day += timedelta(days=1)


def collect_games(begin: date, finish: date):
    events = {}
    for day in dates_between(begin, finish):
        try:
            data = fetch_json(f"{BASE}/{SPORTS['nba']}/scoreboard",
                              {"dates": day.strftime("%Y%m%d"), "limit": 100})
            for game in data.get("events", []):
                if not game.get("status", {}).get("type", {}).get("completed"):
                    continue
                competitors = game.get("competitions", [{}])[0].get("competitors", [])
                if len(competitors) != 2:
                    continue
                events[str(game["id"])] = {
                    "id": str(game["id"]), "date": game["date"],
                    "name": game.get("name", ""),
                    "teams": [{"id": str(c["team"]["id"]), "name": c["team"].get("displayName", "")}
                              for c in competitors],
                }
        except (RuntimeError, ValueError, KeyError) as exc:
            print(f"WARN scoreboard {day}: {exc}")
    return sorted(events.values(), key=lambda e: (e["date"], e["id"]))


def backtest(begin: date, finish: date, max_games: int = 180, lookback: int = 10):
    games = collect_games(begin, finish)[:max_games]
    if len(games) < 20:
        raise RuntimeError(f"Too few completed games found: {len(games)}")
    history = defaultdict(list)
    rows, warnings, processed = [], [], 0

    for event in games:
        try:
            summary = fetch_json(f"{BASE}/{SPORTS['nba']}/summary", {"event": event["id"]})
            extracted = {}
            for team in event["teams"]:
                tid = team["id"]
                values, roster = nba_stats(summary, tid)
                flags = nba_starter_flags(summary, tid)
                official = _q1_team_score(summary, tid)
                observed = sum(float(p.get("points", 0)) for p in values.values())
                points_valid = official is not None and abs(observed - official) <= 0.01
                extracted[tid] = {
                    "values": values, "roster": {str(x) for x in roster},
                    "starters": flags, "points_valid": points_valid,
                }

            # Target-game starters are used only as a retrospective oracle gate.
            # Every estimate and every feature is computed before current game
            # results are appended into team history.
            for team in event["teams"]:
                tid = team["id"]
                today = extracted[tid]
                earlier = history[tid][-lookback:]
                if len(earlier) < 3:
                    continue
                for pid in today["roster"]:
                    if today["starters"].get(pid) is not True:
                        continue
                    for metric in ("points", "rebounds", "assists"):
                        if metric == "points" and not today["points_valid"]:
                            continue
                        samples, flags = [], []
                        for previous in reversed(earlier):  # newest -> oldest
                            if pid in previous["roster"]:
                                samples.append(float(previous["values"].get(pid, {}).get(metric, 0)))
                                flags.append(previous["starters"].get(pid))
                        if len(samples) < 3:
                            continue
                        baseline = project(samples)
                        if baseline["estimate"] < MIN_DISPLAY[metric]:
                            continue
                        naive = round(sum(samples[:3]) / 3, 1)
                        shadow = role_challenger(samples, flags, baseline["estimate"])
                        actual = float(today["values"].get(pid, {}).get(metric, 0))
                        rows.append({
                            "date": parse_time(event["date"]).astimezone(LOCAL_TZ).date().isoformat(), "game_id": event["id"],
                            "matchup": event["name"], "team": team["name"],
                            "player_id": pid,
                            "player": today["values"].get(pid, {}).get("name", pid),
                            "metric": metric, "prior_games": len(samples),
                            "baseline": baseline["estimate"], "naive_last3": naive,
                            "shadow_role": shadow, "actual": actual,
                            "baseline_abs_err": round(abs(baseline["estimate"] - actual), 3),
                            "naive_abs_err": round(abs(naive - actual), 3),
                            "shadow_abs_err": round(abs(shadow - actual), 3) if shadow is not None else "",
                        })

            for team in event["teams"]:
                history[team["id"]].append(extracted[team["id"]])
            processed += 1
        except (RuntimeError, KeyError, ValueError, TypeError) as exc:
            warnings.append(f"{event['id']}: {exc}")
        if processed % 15 == 0 and processed:
            print(f"Progress {processed} games, {len(rows)} player projections", flush=True)
        time.sleep(0.35)

    # A final-date holdout is disjoint from earlier evaluation dates.
    days = sorted({row["date"] for row in rows})
    last_days = set(days[max(0, int(len(days) * 0.70)):])
    output = {"begin": begin.isoformat(), "through": finish.isoformat(),
              "games_found": len(games), "games_processed": processed,
              "rows": len(rows), "holdout_dates": sorted(last_days),
              "method": "strictly prior observations, oracle confirmed-starter identity (hindsight availability)",
              "warning": "No historical pre-tipoff starter snapshots or betting prices. Not a real pregame win-rate or ROI.",
              "warnings": warnings, "by_market": []}
    for metric in ("points", "rebounds", "assists"):
        subset = [r for r in rows if r["metric"] == metric and r["date"] in last_days]
        paired = [r for r in subset if r["shadow_role"] is not None]
        if not subset:
            continue
        output["by_market"].append({
            "market": metric, "holdout_n": len(subset),
            "baseline_mae": round(statistics.mean(r["baseline_abs_err"] for r in subset), 3),
            "naive_last3_mae": round(statistics.mean(r["naive_abs_err"] for r in subset), 3),
            "role_paired_n": len(paired),
            "baseline_mae_on_role_pair": round(statistics.mean(r["baseline_abs_err"] for r in paired), 3) if paired else None,
            "role_challenger_mae": round(statistics.mean(r["shadow_abs_err"] for r in paired), 3) if paired else None,
            "role_challenger_promoted": False,
        })

    folder = Path("backtests")
    folder.mkdir(exist_ok=True)
    stem = f"nba_{begin}_{finish}"
    (folder / f"{stem}.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    fields = ["date", "game_id", "matchup", "team", "player_id", "player", "metric", "prior_games",
              "baseline", "naive_last3", "shadow_role", "actual", "baseline_abs_err",
              "naive_abs_err", "shadow_abs_err"]
    with (folder / f"{stem}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(output, indent=2), flush=True)
    return output


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--from-date", type=date.fromisoformat, default=date(2026, 10, 20))
    p.add_argument("--through-date", type=date.fromisoformat, default=date(2026, 11, 12))
    p.add_argument("--max-games", type=int, default=180)
    p.add_argument("--lookback", type=int, default=10)
    a = p.parse_args()
    backtest(a.from_date, a.through_date, a.max_games, a.lookback)
