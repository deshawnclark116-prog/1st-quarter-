"""Independent NBA Q1 opportunity layer: historical on-court minutes + team matchup.

Inputs for forecast are strictly historical. Historical target starters in the
research runner are hindsight oracle signals, not pre-tipoff confirmations.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict


def q1_clock_seconds(play: dict) -> float | None:
    clock = str(play.get("clock", {}).get("displayValue", ""))
    try:
        pieces = clock.split(":")
        if len(pieces) != 2:
            return None
        remaining = 60 * float(pieces[0]) + float(pieces[1])
        return min(720.0, max(0.0, 720.0 - remaining))
    except (TypeError, ValueError):
        return None


def q1_minutes(summary: dict, team_id: str) -> dict[str, float] | None:
    """Reconstruct player on-court minutes from real starting five and substitutions.

    Reject partial/ambiguous event streams instead of inventing rotation minutes.
    """
    from quarter_challenger import nba_starter_flags
    starter = nba_starter_flags(summary, team_id)
    oncourt = {pid for pid, is_starter in starter.items() if is_starter}
    if len(oncourt) != 5:
        return None
    elapsed = 0.0
    seconds = defaultdict(float)
    subs = 0
    plays = summary.get("plays", [])
    if not plays:
        return None
    for play in plays:
        if play.get("period", {}).get("number") != 1:
            continue
        t = q1_clock_seconds(play)
        if t is None:
            continue
        if t < elapsed - 2.0:
            return None  # PBP out of order or clock correction
        t = max(elapsed, t)
        dt = t - elapsed
        for player in oncourt:
            seconds[player] += dt
        elapsed = t
        if str(play.get("team", {}).get("id")) != str(team_id):
            continue
        if "substitution" not in play.get("type", {}).get("text", "").lower():
            continue
        parts = play.get("participants", [])
        if len(parts) < 2:
            return None
        incoming = str(parts[0].get("athlete", {}).get("id", ""))
        outgoing = str(parts[1].get("athlete", {}).get("id", ""))
        if not incoming or not outgoing or outgoing not in oncourt:
            return None
        oncourt.remove(outgoing)
        oncourt.add(incoming)
        if len(oncourt) != 5:
            return None
        subs += 1
    if elapsed < 690:  # require all but final 30 seconds represented
        return None
    dt = 720 - elapsed
    for player in oncourt:
        seconds[player] += dt
    total = sum(seconds.values())
    if abs(total - 3600) > 3:
        return None
    return {pid: round(value / 60, 3) for pid, value in seconds.items()}


def weighted(values: list[float], recency: bool = True) -> float:
    if not values:
        return 0.0
    # values supplied most recent first
    w = list(range(len(values), 0, -1)) if recency else [1] * len(values)
    return sum(v * weight for v, weight in zip(values, w)) / sum(w)


def weighted_median(values: list[float], recency: bool = True) -> float:
    """L1-optimal conditional forecast under empirical recency weighting."""
    if not values:
        return 0.0
    w = list(range(len(values), 0, -1)) if recency else [1] * len(values)
    pairs = sorted(zip(values, w))
    half = sum(w) / 2
    running = 0
    for value, weight in pairs:
        running += weight
        if running >= half:
            return float(value)
    return float(pairs[-1][0])


def candidate_forecasts(samples: list[dict], metric: str,
                        baseline: float, opponent_allowed: list[float] | None = None,
                        current_team_scored: list[float] | None = None) -> dict[str, float | None]:
    """Several genuinely different structures, all with only pregame features.

    Samples newest-first. Each sample: value, minutes, starter, team_scored.
    No label, live target minutes, or future outcome enters the estimator.
    """
    results = {}
    start_samples = [r for r in samples if r["starter"] is True and r.get("minutes") is not None and r["minutes"] >= 3]
    minutes_samples = [r for r in samples if r.get("minutes") is not None and r["minutes"] >= 2]
    vals = [float(r["value"]) for r in samples]
    results["weighted_median"] = weighted_median(vals)
    results["median_recent6"] = weighted_median(vals[:6])
    results["rolling_mean"] = weighted(vals)
    if len(start_samples) >= 3:
        values = [float(r["value"]) for r in start_samples[:8]]
        minutes = [float(r["minutes"]) for r in start_samples[:8]]
        # Separate opportunities from efficiency: usage/minutes averages.
        mins_pred = min(12, max(4.0, 0.65 * weighted(minutes[:5]) + 0.35 * 8.5))
        rate = weighted([v / max(m, 1) for v, m in zip(values, minutes)])
        volume = weighted(values)
        results["starter_conditional"] = volume
        results["minute_rate"] = rate * mins_pred
        results["minute_half_shrink"] = 0.5 * (rate * mins_pred) + 0.5 * baseline
        # L1 role conditional outcome
        results["starter_median"] = weighted_median(values)
    else:
        for key in ("starter_conditional", "minute_rate", "minute_half_shrink", "starter_median"):
            results[key] = None

    if opponent_allowed and current_team_scored:
        # Score-normalization, shrunk toward 1.0; no target score is used.
        prior_score = weighted(current_team_scored[:8])
        defense = weighted(opponent_allowed[:8])
        if prior_score >= 10 and defense >= 10:
            target_score = 0.70 * prior_score + 0.30 * defense
            scale = max(0.88, min(1.12, target_score / prior_score))
            results["defense_shrink"] = baseline * (0.5 + 0.5 * scale)
            results["defense_full"] = baseline * scale
        else:
            results["defense_shrink"] = None
            results["defense_full"] = None
    else:
        results["defense_shrink"] = None
        results["defense_full"] = None

    # Pooled expectation/median can reflect quantile asymmetry for Q1.
    results["median_half_shrink"] = 0.50 * results["weighted_median"] + 0.50 * baseline
    results["median_quarter_shrink"] = 0.25 * results["weighted_median"] + 0.75 * baseline
    return {name: round(max(0.0, number), 3) if number is not None and math.isfinite(number)
            else None for name, number in results.items()}
