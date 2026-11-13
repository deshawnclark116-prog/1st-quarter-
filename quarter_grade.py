"""Immutable pregame prediction snapshots and future-only first-quarter grading.

Evaluations are projection diagnostics (MAE, bias, range coverage), NEVER
a claim of profitable betting. Games must be final and snapshots pre-kickoff.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from quarterbot import BASE, SPORTS, fetch_json, nba_stats, nfl_stats, parse_time

ARCHIVE_DIR = Path("archive")


def archive_report(report: dict, root: Path = ARCHIVE_DIR) -> int:
    """Capture prediction data before the game, never backfill as a pregame pick."""
    now = datetime.now(timezone.utc)
    count = 0
    for game in report.get("games", []):
        tip = parse_time(game["date"])
        if not (now + timedelta(minutes=2) < tip <= now + timedelta(hours=2)):
            continue
        if not game.get("predictions"):
            continue
        if game["sport"] == "nba" and game.get("lineup_status") != "confirmed":
            continue
        if game["sport"] == "nfl" and not game.get("availability_source"):
            continue
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        path = root / game["date_et"][:10] / f"{game['sport']}_{game['id']}_{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            continue
        snapshot = {
            "snapshot_at": now.isoformat(), "model_version": "q1_baseline_v1",
            "game_id": game["id"], "sport": game["sport"], "date": game["date"],
            "date_et": game["date_et"], "name": game["name"], "teams": game["teams"],
            "lineup_status": game.get("lineup_status"),
            "lineup_source": game.get("lineup_source"),
            "availability_source": game.get("availability_source"),
            "predictions": game["predictions"],
        }
        path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        count += 1
    return count


def _final(summary: dict) -> bool:
    return any(c.get("status", {}).get("type", {}).get("completed") is True
               or c.get("status", {}).get("type", {}).get("name") == "STATUS_FINAL"
               for c in summary.get("header", {}).get("competitions", []))


def select_latest_pregame(root: Path = ARCHIVE_DIR) -> list[dict]:
    latest = {}
    if not root.exists():
        return []
    for file in root.rglob("*.json"):
        try:
            snapshot = json.loads(file.read_text(encoding="utf-8"))
            at = parse_time(snapshot["snapshot_at"])
            if at >= parse_time(snapshot["date"]) - timedelta(minutes=2):
                continue
            if not snapshot.get("predictions"):
                continue
            if snapshot["sport"] == "nba" and snapshot.get("lineup_status") != "confirmed":
                continue
            if snapshot["sport"] == "nfl" and not snapshot.get("availability_source"):
                continue
            key = (snapshot["sport"], str(snapshot["game_id"]))
            if key not in latest or at > parse_time(latest[key]["snapshot_at"]):
                latest[key] = snapshot
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return sorted(latest.values(), key=lambda s: s["date"])


def _q1_team_score(summary: dict, team_id: str):
    for competition in summary.get("header", {}).get("competitions", []):
        for entry in competition.get("competitors", []):
            if str(entry.get("id", entry.get("team", {}).get("id"))) != str(team_id):
                continue
            scores = entry.get("linescores", [])
            if scores:
                try:
                    return int(float(scores[0].get("value", scores[0].get("displayValue"))))
                except (ValueError, TypeError):
                    pass
    return None


def grade_snapshot(snapshot: dict, summary: dict) -> tuple[list[dict], list[str]]:
    if not _final(summary):
        return [], ["Game not final"]
    if snapshot["sport"] == "nba" and not summary.get("plays"):
        return [], ["NBA first-quarter play-by-play unavailable"]
    if snapshot["sport"] == "nfl" and not summary.get("drives", {}).get("previous"):
        return [], ["NFL drives unavailable"]
    roster, stats, warnings = set(), {}, []
    for team in snapshot["teams"]:
        tid = str(team["id"])
        player_stats, members = (nba_stats(summary, tid) if snapshot["sport"] == "nba"
                                 else nfl_stats(summary, tid))
        roster.update((tid, str(pid)) for pid in members)
        stats.update({(tid, str(pid)): data for pid, data in player_stats.items()})
        if snapshot["sport"] == "nba":
            official = _q1_team_score(summary, tid)
            computed = sum(float(p.get("points", 0)) for p in player_stats.values())
            if official is None or abs(computed - official) > 0.01:
                warnings.append(f"team {tid}: Q1 points failed reconciliation (PBP={computed}, official={official})")

    rows = []
    for pred in snapshot["predictions"]:
        key = str(pred["team_id"]), str(pred["player_id"])
        if key not in roster:  # unknown DNP is not an observed zero
            continue
        if snapshot["sport"] == "nba" and pred["metric"] == "points" and warnings:
            continue
        actual = float(stats.get(key, {}).get(pred["metric"], 0))
        estimate = float(pred["estimate"])
        rows.append({
            "date_et": snapshot["date_et"][:10], "game_id": snapshot["game_id"],
            "matchup": snapshot["name"], "sport": snapshot["sport"],
            "snapshot_at": snapshot["snapshot_at"], "model_version": snapshot["model_version"],
            "player": pred["player"], "metric": pred["metric"],
            "estimate": estimate, "actual": actual, "error": round(estimate - actual, 3),
            "absolute_error": round(abs(estimate - actual), 3),
            "in_range": bool(float(pred["low"]) <= actual <= float(pred["high"])),
        })
    return rows, warnings


def summarize(rows: list[dict]) -> dict:
    groups = defaultdict(list)
    for row in rows:
        groups[row["sport"], row["metric"]].append(row)
    return {
        "sampled_games": len({(r["sport"], r["game_id"]) for r in rows}),
        "graded_projections": len(rows),
        "by_market": [
            {"sport": sport, "market": metric, "n": len(data),
             "mae": round(sum(x["absolute_error"] for x in data) / len(data), 3),
             "bias": round(sum(x["error"] for x in data) / len(data), 3),
             "empirical_range_coverage": round(sum(x["in_range"] for x in data) / len(data), 3)}
            for (sport, metric), data in sorted(groups.items())
        ],
        "note": "Accuracy diagnostics only; no odds, betting lines, holdout or net ROI measured.",
    }


def main():
    rows, warnings = [], []
    snapshots = select_latest_pregame()
    for snap in snapshots:
        try:
            summary = fetch_json(f"{BASE}/{SPORTS[snap['sport']]}/summary", {"event": snap["game_id"]})
            graded, notes = grade_snapshot(snap, summary)
            rows.extend(graded)
            warnings.extend(f"{snap['game_id']}: {note}" for note in notes)
        except (RuntimeError, ValueError, KeyError) as exc:
            warnings.append(f"{snap['game_id']}: {exc}")
    dest = Path("grading")
    dest.mkdir(exist_ok=True)
    result = summarize(rows)
    result["snapshots_checked"] = len(snapshots)
    result["warnings"] = warnings
    (dest / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    fields = ["date_et", "game_id", "matchup", "sport", "snapshot_at", "model_version", "player", "metric", "estimate", "actual", "error", "absolute_error", "in_range"]
    with (dest / "rows.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Graded {len(rows)} projections from {len(snapshots)} pregame snapshots")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
