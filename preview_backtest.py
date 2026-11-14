"""Retrospective grading of immutable, pre-tipoff UNCONFIRMED NBA Q1 previews.

Strictly uses saved predictions, never reconstructs the model after the event.
Scores only fully completed first quarters and reconciled Q1 play-by-play.
These were NOT official published, verified-lineup picks. No odds/ROI recorded.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from quarterbot import BASE, SPORTS, fetch_json, nba_stats, parse_time
from quarter_grade import _q1_team_score


INPUTS = (
    Path("preview_snapshots/2026-11-13_7pm.json"),
    Path("early_preview_8pm.json"),
)
OUTPUT = Path("preview_grading")
FIELDS = ("game_id", "game", "tipoff_et", "captured_at", "player", "player_id",
          "team", "team_id", "metric", "estimate", "actual", "error", "absolute_error",
          "low", "high", "within_range", "shadow_shot_estimate",
          "shadow_shot_ae", "shadow_role_estimate", "shadow_role_ae",
          "pre_tipoff_minutes", "preview_only", "confirmed_lineup")


def means(rows, metric_name=None):
    data = [row for row in rows if metric_name is None or row["metric"] == metric_name]
    if not data:
        return {"n": 0}
    ae = [row["absolute_error"] for row in data]
    bias = [row["error"] for row in data]
    return {
        "n": len(data),
        "mae": round(sum(ae) / len(ae), 3),
        "bias_projected_minus_actual": round(sum(bias) / len(bias), 3),
        "mean_projection": round(sum(row["estimate"] for row in data) / len(data), 3),
        "mean_actual": round(sum(row["actual"] for row in data) / len(data), 3),
        "within_one": sum(x <= 1 for x in ae),
        "within_two": sum(x <= 2 for x in ae),
        "range_coverage": round(sum(row["within_range"] for row in data) / len(data), 3),
        "zero_actuals": sum(row["actual"] == 0 for row in data),
    }


def paired_models(rows):
    data = [row for row in rows if row["metric"] == "points"
            and row["shadow_shot_ae"] is not None]
    if not data:
        return {"n": 0}
    b = sum(row["absolute_error"] for row in data) / len(data)
    s = sum(row["shadow_shot_ae"] for row in data) / len(data)
    return {
        "n": len(data),
        "baseline_mae_paired": round(b, 3),
        "shot_mae_paired": round(s, 3),
        "baseline_minus_shot_mae": round(b - s, 3),
        "shot_improvement_pct": round(100 * (b - s) / b, 2) if b else None,
        "shot_better_n": sum(row["shadow_shot_ae"] < row["absolute_error"] for row in data),
        "baseline_better_n": sum(row["shadow_shot_ae"] > row["absolute_error"] for row in data),
        "ties": sum(row["shadow_shot_ae"] == row["absolute_error"] for row in data),
        "warning": "Unconfirmed-starter previews, not wagered picks, no sportsbook/ROI evidence.",
    }


def completed_q1(summary: dict, event: dict) -> bool:
    status = event.get("status", {})
    # A valid Q2 or later event demonstrates first-quarter completion.
    if status.get("type", {}).get("completed") is True:
        return True
    if int(status.get("period") or 0) >= 2:
        return True
    return any(p.get("period", {}).get("number", 0) >= 2
               for p in summary.get("plays", []))


def find_event(preview: dict, events: list[dict]) -> dict | None:
    names = {str(p["team"]).casefold() for p in preview.get("projections", [])}
    matches = []
    for event in events:
        comp = (event.get("competitions") or [{}])[0]
        teams = {str(c.get("team", {}).get("displayName", "")).casefold()
                 for c in comp.get("competitors", [])}
        if teams == names and len(teams) == 2:
            matches.append(event)
    return matches[0] if len(matches) == 1 else None


def grade_game(preview: dict, created: datetime, event: dict, summary: dict) -> tuple[list[dict], dict]:
    info = {
        "name": preview["game"], "date_et": preview["date_et"],
        "game_id": str(event["id"]),
        "preview_predictions": len(preview["projections"]),
        "pre_tipoff_snapshot": created.isoformat(),
        "lineups_confirmed_when_predicted": bool(preview.get("confirmed_starters_both_teams")),
        "roster_injury_check": bool(preview.get("roster_injuries_verified")),
        "status": "pending", "warnings": [],
    }
    kickoff = parse_time(event["date"])
    margin = (kickoff - created).total_seconds() / 60
    info["pre_tipoff_minutes"] = round(margin, 2)
    if margin <= 2:
        info["status"] = "excluded_late_snapshot"
        info["warnings"].append("Preview captured within 2 minutes of ESPN scheduled tipoff.")
        return [], info
    if not completed_q1(summary, event):
        info["status"] = "pending_q1"
        return [], info
    official = {}
    player_stats = {}
    roster = set()
    for team_id in {str(p["team_id"]) for p in preview["projections"]}:
        q1 = _q1_team_score(summary, team_id)
        values, members = nba_stats(summary, team_id)
        points = sum(float(v.get("points", 0)) for v in values.values())
        if q1 is None:
            info["warnings"].append(f"{team_id}: official first-quarter line score not available")
            info["status"] = "excluded_no_official_q1"
            return [], info
        if not math.isclose(points, float(q1), abs_tol=0.01):
            info["warnings"].append(f"{team_id}: Q1 PBP scoring {points} != official {q1}; all markets excluded")
            info["status"] = "excluded_q1_pbp_mismatch"
            return [], info
        official[team_id] = q1
        player_stats.update({(team_id, str(pid)): data for pid, data in values.items()})
        roster.update((team_id, str(pid)) for pid in members)
    if len(official) != 2:
        info["status"] = "excluded_bad_team_count"
        return [], info
    info["official_q1_team_scores"] = official
    info["status"] = "graded"
    rows = []
    for p in preview["projections"]:
        key = (str(p["team_id"]), str(p["player_id"]))
        if key not in roster:
            info["warnings"].append(f"{p['player']} not listed as played in final box score; excluded instead of zero")
            continue
        actual = float(player_stats.get(key, {}).get(p["metric"], 0))
        baseline = float(p["estimate"])
        shot = p.get("shadow_shot_estimate") if p["metric"] == "points" else None
        role = p.get("shadow_role_estimate")
        shot = float(shot) if shot is not None else None
        role = float(role) if role is not None else None
        rows.append({
            "game_id": str(event["id"]), "game": preview["game"],
            "tipoff_et": preview["date_et"], "captured_at": created.isoformat(),
            "player": p["player"], "player_id": str(p["player_id"]),
            "team": p["team"], "team_id": str(p["team_id"]),
            "metric": p["metric"], "estimate": baseline, "actual": actual,
            "error": round(baseline - actual, 3),
            "absolute_error": round(abs(baseline - actual), 3),
            "low": p["low"], "high": p["high"],
            "within_range": float(p["low"]) <= actual <= float(p["high"]),
            "shadow_shot_estimate": shot,
            "shadow_shot_ae": round(abs(shot - actual), 3) if shot is not None else None,
            "shadow_role_estimate": role,
            "shadow_role_ae": round(abs(role - actual), 3) if role is not None else None,
            "pre_tipoff_minutes": round(margin, 2), "preview_only": True,
            "confirmed_lineup": bool(preview.get("confirmed_starters_both_teams")),
        })
    info["graded_projections"] = len(rows)
    info["excluded_unverified_participation"] = len(preview["projections"]) - len(rows)
    info["by_market"] = {
        metric: means(rows, metric) for metric in ("points", "rebounds", "assists")
    }
    info["shot_model_comparison"] = paired_models(rows)
    info["largest_misses"] = [
        {"player": r["player"], "metric": r["metric"], "estimate": r["estimate"],
         "actual": r["actual"], "absolute_error": r["absolute_error"]}
        for r in sorted(rows, key=lambda x: x["absolute_error"], reverse=True)[:10]
    ]
    return rows, info


def score_previews(paths=INPUTS, output=OUTPUT):
    previews = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        at = parse_time(payload["created_at_utc"])
        for game in payload["games"]:
            previews.append((game, at, str(path)))
    dates = {p["date_et"][:10].replace("-", "") for p, _, _ in previews}
    scoreboard = []
    for d in sorted(dates):
        data = fetch_json(f"{BASE}/{SPORTS['nba']}/scoreboard", {"dates": d, "limit": 100})
        scoreboard.extend(data.get("events", []))
    rows = []
    game_report = []
    for p, at, path in previews:
        event = find_event(p, scoreboard)
        if event is None:
            game_report.append({"name": p["game"], "status": "no_unambiguous_espn_game_id",
                                "preview_source": path, "preview_predictions": len(p["projections"])})
            continue
        try:
            summary = fetch_json(f"{BASE}/{SPORTS['nba']}/summary", {"event": event["id"]})
            graded, result = grade_game(p, at, event, summary)
            result["preview_source"] = path
            rows.extend(graded)
            game_report.append(result)
        except (RuntimeError, KeyError, ValueError, TypeError) as error:
            game_report.append({"name": p["game"], "status": "data_error",
                                "message": str(error), "preview_source": path})
    output.mkdir(parents=True, exist_ok=True)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "Immutable 2026-11-13 7 PM and 8 PM pre-tipoff preview files",
        "status_note": "NOT official picks: no confirmed lineups; no line/odds; zero betting ROI evidence.",
        "total_preview_games": len(previews),
        "eligible_graded_games": sum(r["status"] == "graded" for r in game_report),
        "pending_or_excluded_games": sum(r["status"] != "graded" for r in game_report),
        "total_saved_preview_predictions": sum(len(p["projections"]) for p, _, _ in previews),
        "graded_predictions": len(rows),
        "by_market": {m: means(rows, m) for m in ("points","rebounds","assists")},
        "paired_shot_comparison": paired_models(rows),
        "by_game": game_report,
        "largest_misses_overall": [
            {"game": r["game"], "player": r["player"], "metric": r["metric"],
             "estimate": r["estimate"], "actual": r["actual"],
             "absolute_error": r["absolute_error"]}
            for r in sorted(rows, key=lambda r:r["absolute_error"], reverse=True)[:25]
        ],
        "caveat": "This is a retrospective SCORE of pre-recorded predictions, not an independently clean betting backtest: lineups were not verified and no sportsbook lines or odds were stored.",
    }
    (output/"2026-11-13_summary.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    with (output/"2026-11-13_rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, indent=2), flush=True)
    return summary


if __name__ == "__main__":
    score_previews()
