"""Chronological Q1 opportunity experiments, never overwrite the production champion.

Record all candidates before examining the test period. Historic target
starting fives are known from final boxscores (ORACLE availability);
therefore this is research, not a pregame betting-performance claim.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

from nba_walkforward import collect_games
from quarterbot import BASE, SPORTS, LOCAL_TZ, MIN_DISPLAY, fetch_json, nba_stats, parse_time, project
from quarter_challenger import nba_starter_flags
from quarter_grade import _q1_team_score
from quarter_opportunity import q1_minutes, candidate_forecasts, weighted
from quarter_shots import extract_q1_shots, fullgame_shots, forecast_shots


def mean(values: list[float]):
    return sum(values) / len(values) if values else None


def samples_before(history: list, player_id: str, metric: str) -> list[dict]:
    output = []
    for game in reversed(history):
        if player_id not in game["roster"]:
            continue
        output.append({
            "value": float(game["values"].get(player_id, {}).get(metric, 0)),
            "minutes": (game["minutes"] or {}).get(player_id),
            "starter": game["starters"].get(player_id),
            "team_scored": game["scored"],
            "q1_shots": game["q1_shots"].get(player_id, {}),
            "full_shots": game["full_shots"].get(player_id, {}),
        })
    return output


def walkforward(begin: date, finish: date, max_games: int = 250, lookback: int = 10,
                output_stem: str = "opportunity-v3-2025"):
    games = collect_games(begin, finish)[:max_games]
    if len(games) < 45:
        raise RuntimeError(f"too few completed games {len(games)}")
    history = defaultdict(list)
    rows, warnings = [], []
    minute_success = minute_fail = 0
    for idx, event in enumerate(games):
        try:
            summary = fetch_json(f"{BASE}/{SPORTS['nba']}/summary", {"event": event["id"]})
            data = {}
            for team in event["teams"]:
                tid = team["id"]
                values, roster = nba_stats(summary, tid)
                starters = nba_starter_flags(summary, tid)
                minutes = q1_minutes(summary, tid)
                if minutes is None:
                    minute_fail += 1
                else:
                    minute_success += 1
                official = _q1_team_score(summary, tid)
                points_sum = sum(float(p.get("points", 0)) for p in values.values())
                data[tid] = {
                    "values": values, "roster": {str(pid) for pid in roster},
                    "starters": starters, "minutes": minutes,
                    "q1_shots": extract_q1_shots(summary, tid),
                    "full_shots": fullgame_shots(summary, tid),
                    "scored": official,
                    "points_valid": official is not None and abs(official - points_sum) < 0.01,
                }
            teamids = [t["id"] for t in event["teams"]]
            for team in event["teams"]:
                tid = team["id"]
                opp_tid = next(t for t in teamids if t != tid)
                older = history[tid][-lookback:]
                opposition = history[opp_tid][-lookback:]
                current = data[tid]
                if len(older) < 3:
                    continue
                q1_scores = [float(g["scored"]) for g in reversed(older) if g["scored"] is not None]
                # Opponent's Q1 points allowed tracked in prior games only.
                opp_allowed = [float(g["allowed"]) for g in reversed(opposition)
                               if g.get("allowed") is not None]
                for pid in current["roster"]:
                    if current["starters"].get(pid) is not True:
                        continue
                    for metric in ("points", "rebounds", "assists"):
                        if metric == "points" and not current["points_valid"]:
                            continue
                        samples = samples_before(older, pid, metric)
                        if len(samples) < 3:
                            continue
                        base = project([s["value"] for s in samples])["estimate"]
                        if base < MIN_DISPLAY[metric]:
                            continue
                        candidates = candidate_forecasts(samples, metric, base, opp_allowed, q1_scores)
                        if metric == "points":
                            starter_mins = [r["minutes"] for r in samples
                                            if r.get("starter") is True and isinstance(r.get("minutes"), (int,float))]
                            expected_mins = (min(12.0, max(4.0, 0.65 * weighted(starter_mins[:5]) + 0.35 * 8.5))
                                             if len(starter_mins) >= 3 else None)
                            team_scale = None
                            if opp_allowed and q1_scores:
                                score = weighted(q1_scores[:8])
                                opponent = weighted(opp_allowed[:8])
                                if score >= 10 and opponent >= 10:
                                    team_scale = max(0.88, min(1.12, (0.7*score + 0.3*opponent)/score))
                            candidates.update(forecast_shots(samples, base, expected_mins, team_scale))
                        else:
                            candidates.update({k: None for k in ("shot_volume", "shot_half_shrink",
                                "shot_starter", "shot_starter_half_shrink", "shot_minutes",
                                "shot_minutes_half_shrink", "shot_team_context")})
                        actual = float(current["values"].get(pid, {}).get(metric, 0))
                        row = {
                            "date": parse_time(event["date"]).astimezone(LOCAL_TZ).date().isoformat(),
                            "game_id": event["id"], "matchup": event["name"], "team": team["name"],
                            "player_id": pid, "metric": metric, "actual": actual,
                            "baseline": base, "baseline_ae": round(abs(base-actual), 4),
                            "prior_n": len(samples),
                        }
                        for name, estimate in candidates.items():
                            row[name] = estimate
                            row[name+"_ae"] = round(abs(estimate-actual), 4) if estimate is not None else None
                        rows.append(row)
            for tid in teamids:
                other = next(t for t in teamids if t != tid)
                data[tid]["allowed"] = data[other]["scored"]
                history[tid].append(data[tid])
        except (RuntimeError, KeyError, TypeError, ValueError) as err:
            warnings.append(f"{event['id']}: {err}")
        if (idx+1) % 30 == 0:
            print(f"{idx+1}/{len(games)} games, {len(rows)} predictions, "
                  f"minutes success={minute_success}, fail={minute_fail}", flush=True)
        time.sleep(0.20)

    names = ["weighted_median", "median_recent6", "rolling_mean", "starter_conditional",
             "minute_rate", "minute_half_shrink", "starter_median",
             "defense_shrink", "defense_full", "median_half_shrink", "median_quarter_shrink",
             "shot_volume", "shot_half_shrink", "shot_starter", "shot_starter_half_shrink",
             "shot_minutes", "shot_minutes_half_shrink", "shot_team_context"]
    train_end = "2025-11-02"  # immutable predeclared research windows
    validation_end = "2025-11-06"
    markets = ["points", "rebounds", "assists"]
    output = {
        "time_window": [begin.isoformat(), finish.isoformat()],
        "training_through": train_end,
        "selection_validation": ["2025-11-03", validation_end],
        "exploratory_diagnostic_test": ["2025-11-07", finish.isoformat()],
        "test_reuse_warning": "2025 is an independent retrospective cross-season stress test, NOT a future pregame holdout; future confirmation required.",
        "oracle_starters": True, "team_minute_reconstruction_success": minute_success,
        "team_minute_reconstruction_failure": minute_fail,
        "completed_games": len(games), "predictions": len(rows),
        "warnings": warnings, "markets": [],
        "promotion_authorized": False,
    }

    for metric in markets:
        subset = [r for r in rows if r["metric"] == metric]
        train = [r for r in subset if r["date"] <= train_end]
        val = [r for r in subset if train_end < r["date"] <= validation_end]
        test = [r for r in subset if r["date"] > validation_end]
        if not train or not val:
            continue

        def evaluate(group, name):
            paired = [r for r in group if r.get(name) is not None]
            if not paired:
                return {"n": 0}
            bb = mean([r["baseline_ae"] for r in paired])
            challenger = mean([r[name+"_ae"] for r in paired])
            return {"n": len(paired), "baseline_mae_paired": round(bb, 4),
                    "challenger_mae": round(challenger, 4),
                    "relative_mae_improvement_pct": round(100 * (bb-challenger) / bb, 2) if bb else None}

        candidates = [{"candidate": name, "train": evaluate(train, name),
                       "validation": evaluate(val, name),
                       "diagnostic_test": evaluate(test, name)} for name in names]
        for item in candidates:
            n = item["validation"]["n"]
            item["eligible_for_selection"] = n >= 80 and n >= int(0.7*len(val))
        eligible = [x for x in candidates if x["eligible_for_selection"]]
        selected = max(eligible, key=lambda x: x["validation"]["relative_mae_improvement_pct"]) if eligible else None
        output["markets"].append({
            "market": metric, "train_n": len(train), "validation_n": len(val),
            "diagnostic_n": len(test),
            "selected_on_validation_only": selected["candidate"] if selected else None,
            "selected_diagnostic_result": selected["diagnostic_test"] if selected else None,
            "baseline_full_diagnostic_mae": round(mean([r["baseline_ae"] for r in test]), 4) if test else None,
            "candidates": candidates,
        })

    dest = Path("research_results")
    dest.mkdir(exist_ok=True)
    (dest / f"{output_stem}.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    if rows:
        with (dest / f"{output_stem}.csv").open("w", newline="", encoding="utf-8") as handle:
            fields = list(rows[0])
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(output, indent=2), flush=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", type=date.fromisoformat, default=date(2025,10,20))
    parser.add_argument("--through-date", type=date.fromisoformat, default=date(2025,11,12))
    parser.add_argument("--max-games", type=int, default=180)
    args = parser.parse_args()
    walkforward(args.from_date, args.through_date, args.max_games)
