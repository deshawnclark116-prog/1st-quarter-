"""Role-conditioned NBA first-quarter shadow model; not approved for picks.

Evaluate against the production baseline on identical future held-out games.
"""
from __future__ import annotations


def nba_starter_flags(summary: dict, team_id: str) -> dict[str, bool]:
    flags = {}
    for group in summary.get("boxscore", {}).get("players", []):
        if str(group.get("team", {}).get("id")) != str(team_id):
            continue
        for category in group.get("statistics", []):
            for entry in category.get("athletes", []):
                pid = entry.get("athlete", {}).get("id")
                if pid is not None and isinstance(entry.get("starter"), bool):
                    flags[str(pid)] = entry["starter"]
    return flags


def role_challenger(values: list[float], flags: list[bool | None], baseline: float) -> float | None:
    """Shrink starter-only weighted mean toward baseline, or abstain if insufficient."""
    if len(values) != len(flags):
        return None
    starts = [v for v, flag in zip(values, flags) if flag is True]
    if len(starts) < 3:
        return None
    weights = list(range(len(starts), 0, -1))  # values are newest-first
    weighted = sum(v * w for v, w in zip(starts, weights)) / sum(weights)
    reliability = len(starts) / (len(starts) + 4)
    return round(max(0.0, reliability * weighted + (1 - reliability) * baseline), 1)
