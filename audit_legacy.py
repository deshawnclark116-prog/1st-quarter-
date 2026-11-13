"""Audit a historical pregame report without presenting it as verified betting picks.

The old baseline had no confirmed-starter snapshots; therefore its errors are
labelled legacy/unverified and never mixed into the clean future-only ledger.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from datetime import timedelta
from pathlib import Path
from quarterbot import BASE, SPORTS, fetch_json, parse_time
from quarter_grade import grade_snapshot, summarize


def run_audit(ref: str, day: str) -> dict:
    raw = subprocess.check_output(["git", "show", f"{ref}:predictions.json"], text=True)
    report = json.loads(raw)
    at = parse_time(report["generated_at"])
    rows, warnings, games = [], [], []
    for game in report.get("games", []):
        if game["date_et"][:10] != day or not game.get("predictions"):
            continue
        tipoff = parse_time(game["date"])
        if at >= tipoff - timedelta(minutes=2):
            warnings.append(f"{game['name']}: original snapshot was NOT pregame; excluded")
            continue
        snap = {"snapshot_at": report["generated_at"], "model_version": "legacy_unverified",
                "game_id": game["id"], "sport": game["sport"], "date": game["date"],
                "date_et": game["date_et"], "name": game["name"],
                "teams": game["teams"], "predictions": game["predictions"]}
        try:
            source = fetch_json(f"{BASE}/{SPORTS[game['sport']]}/summary", {"event": game["id"]})
            graded, notes = grade_snapshot(snap, source)
            rows.extend(graded)
            warnings.extend(f"{game['name']}: {note}" for note in notes)
            games.append({"game": game["name"], "sport": game["sport"],
                          "predictions": len(game["predictions"]),
                          "verified_player_grade_rows": len(graded),
                          "errors": notes})
        except (RuntimeError, ValueError, KeyError) as exc:
            warnings.append(f"{game['name']}: {exc}")
    result = summarize(rows)
    result.update({"reference_commit": ref, "day": day, "generated_at": report["generated_at"],
                   "status": "legacy_unverified_lineups_no_betting_lines",
                   "by_game": games, "warnings": warnings})
    dest = Path("legacy_audit")
    dest.mkdir(exist_ok=True)
    (dest / f"{day}.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    fields = ["date_et", "game_id", "matchup", "sport", "snapshot_at", "model_version",
              "player", "metric", "estimate", "actual", "error", "absolute_error", "in_range",
              "shadow_estimate", "shadow_absolute_error"]
    with (dest / f"{day}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--ref", default="e68f587c8")
    cli.add_argument("--date", default="2026-11-12")
    args = cli.parse_args()
    run_audit(args.ref, args.date)
