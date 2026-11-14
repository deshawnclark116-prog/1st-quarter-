"""Low-cost GitHub cron preflight for per-game Q1 primary and retry checks."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from quarterbot import LOCAL_TZ, upcoming_games, parse_time
from quarter_schedule import game_phase, index_previous, reusable_verified


def should_run(now: datetime, games: list[dict], old: dict | None) -> tuple[bool, list[str]]:
    known = index_previous(old)
    targets = []
    for game in games:
        phase = game_phase(game["date"], now)
        if phase not in {"primary", "retry"}:
            continue
        old_game = known.get((game["sport"], str(game["id"])))
        if not reusable_verified(game, old_game):
            targets.append(f"{game['sport']} {game['name']} {phase} ({game['date_et']})")
    if targets:
        return True, targets
    # Remove already-started games from the public latest report even if
    # there are no new pregame candidates to score at this tick.
    if old and any(g.get("predictions") and parse_time(g["date"]) <= now
                   for g in old.get("games", [])):
        return True, ["Clear finished games from public latest report"]
    return False, []


def main() -> int:
    event = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    run = event != "schedule"
    targets = []
    if not run:
        now = datetime.now(timezone.utc)
        old_path = Path("predictions.json")
        old = None
        if old_path.exists():
            try:
                old = json.loads(old_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                print("Previous report unreadable; preflight will recompute if a game is due.")
        errors = []
        games = []
        for sport in ("nba", "nfl"):
            try:
                games.extend(upcoming_games(sport, now.astimezone(LOCAL_TZ).date(), 2))
            except (RuntimeError, KeyError, TypeError, ValueError) as exc:
                errors.append(f"{sport}: {exc}")
        if errors:
            print("PREFLIGHT WARNINGS:", *errors, sep="\n")
            if not games:
                raise RuntimeError("All schedule queries failed; cannot determine game triggers.")
        run, targets = should_run(now, games, old)
    print("Q1 preflight:", "RUN" if run else "SKIP", *targets, sep="\n")
    print("Q1 preflight trigger:", event, "(scheduled cron versus explicit code/manual trigger)")
    print("Q1 request: immediate November 13 8 PM preview before scheduled tipoff.")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"run={'true' if run else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
