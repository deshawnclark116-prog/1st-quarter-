"""Game-local primary/backup windows for 1st Quarter publication.

GitHub cron fires every 15 minutes, not at arbitrary kickoff timestamps.
A primary candidate is evaluated at ~T-30 minutes, and an unconfirmed
game is retried around T-15. Late/missed runs cannot publish after T-2.
"""
from __future__ import annotations

from datetime import datetime, timezone


def game_phase(game_date: str, now: datetime) -> str:
    """Use actual scheduled ESPN tipoff/kickoff in UTC, never wall-clock hours."""
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    start = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
    remaining = (start.astimezone(timezone.utc) - now.astimezone(timezone.utc)).total_seconds() / 60
    if remaining <= 2:
        return "closed"
    if remaining < 20:
        return "retry"
    if remaining <= 35:
        return "primary"
    return "waiting"


def reusable_verified(game: dict, prior: dict | None) -> bool:
    """Never carry over a different game, changed kickoff or unverified picks."""
    if not prior:
        return False
    if (str(game.get("id")) != str(prior.get("id"))
            or game.get("sport") != prior.get("sport")
            or game.get("date") != prior.get("date")):
        return False
    if game.get("sport") == "nba":
        return bool(prior.get("lineup_status") == "confirmed"
                    and prior.get("lineup_source")
                    and prior.get("predictions")
                    and all(p.get("starter_confirmed") is True for p in prior["predictions"]))
    if game.get("sport") == "nfl":
        return bool(prior.get("availability_source") and prior.get("predictions"))
    return False


def index_previous(report: dict | None) -> dict:
    if not isinstance(report, dict):
        return {}
    return {(str(g.get("sport")), str(g.get("id"))): g
            for g in report.get("games", []) if isinstance(g, dict)}
