"""NBA Q1 shot-volume / shooting-skill model.

Separates expected first-quarter opportunities (2PA, 3PA, FTA) from
shooting ability based on past full-game boxscore splits. All features
are sourced before the target game in the research runner.
"""
from __future__ import annotations

from collections import defaultdict

def weighted(values):
    if not values:
        return 0.0
    weights = list(range(len(values), 0, -1))
    return sum(a*b for a,b in zip(values, weights))/sum(weights)


PRIOR = {"two": (0.53, 20.0), "three": (0.36, 28.0), "free": (0.78, 15.0)}


def extract_q1_shots(summary: dict, team_id: str) -> dict[str, dict[str, float]]:
    """Map ESPN Q1 shootingPlay to per-player attempts and makes."""
    out = defaultdict(lambda: {"2pa":0.0,"2pm":0.0,"3pa":0.0,"3pm":0.0,"fta":0.0,"ftm":0.0})
    for play in summary.get("plays", []):
        if play.get("period", {}).get("number") != 1:
            continue
        if str(play.get("team", {}).get("id")) != str(team_id):
            continue
        if not play.get("shootingPlay"):
            continue
        parts = play.get("participants") or []
        if not parts:
            continue
        pid = parts[0].get("athlete", {}).get("id")
        if not pid:
            continue
        attempted = play.get("pointsAttempted")
        value = {1:("fta","ftm"),2:("2pa","2pm"),3:("3pa","3pm")}.get(attempted)
        if not value:
            continue
        out[str(pid)][value[0]] += 1
        if play.get("scoringPlay"):
            out[str(pid)][value[1]] += 1
    return dict(out)


def fullgame_shots(summary: dict, team_id: str) -> dict[str, dict[str, float]]:
    """Prior full-game boxscore stats to estimate player shooting ability."""
    out = {}
    for group in summary.get("boxscore", {}).get("players", []):
        if str(group.get("team", {}).get("id")) != str(team_id):
            continue
        for category in group.get("statistics", []):
            for athlete in category.get("athletes", []):
                pid = athlete.get("athlete", {}).get("id")
                numbers = athlete.get("stats", [])
                if not pid or len(numbers) < 5:
                    continue
                try:
                    def pair(s):
                        a, b = s.split("-", 1)
                        return float(a), float(b)
                    fg_m, fg_a = pair(numbers[2])
                    t_m, t_a = pair(numbers[3])
                    f_m, f_a = pair(numbers[4])
                    if fg_a < t_a or fg_m < t_m:
                        continue
                except (TypeError, ValueError, AttributeError):
                    continue
                out[str(pid)] = {
                    "2pa": fg_a-t_a, "2pm": fg_m-t_m, "3pa": t_a, "3pm": t_m,
                    "fta": f_a, "ftm": f_m,
                }
    return out


def _shrink_skill(history: list[dict], kind: str) -> float:
    prefix = {"two": "2", "three": "3", "free": "ft"}[kind]
    made = {"two": "2pm", "three": "3pm", "free": "ftm"}[kind]
    attempted = {"two": "2pa", "three": "3pa", "free": "fta"}[kind]
    baseline, strength = PRIOR[kind]
    skill_makes = sum(float(row.get("full_shots", {}).get(made, 0)) for row in history[:10])
    skill_attempts = sum(float(row.get("full_shots", {}).get(attempted, 0)) for row in history[:10])
    return (strength*baseline + skill_makes)/(strength + skill_attempts)


def forecast_shots(history: list[dict], baseline: float, projected_minutes: float | None = None,
                   team_scale: float | None = None) -> dict[str,float|None]:
    """One model family with fixed definitions; no bet prices or target labels."""
    if len(history) < 3:
        return {}
    p2, p3, pft = (_shrink_skill(history, kind) for kind in ("two", "three", "free"))
    keys = ("2pa", "3pa", "fta")
    means = {k: weighted([r.get("q1_shots", {}).get(k, 0) for r in history]) for k in keys}
    baseline_shot = 2*means["2pa"]*p2 + 3*means["3pa"]*p3 + means["fta"]*pft
    starters = [r for r in history if r.get("starter") is True]
    starter_result = None
    if len(starters) >= 3:
        volumes = {k: weighted([r.get("q1_shots", {}).get(k,0) for r in starters[:8]]) for k in keys}
        starter_result = 2*volumes["2pa"]*p2 + 3*volumes["3pa"]*p3 + volumes["fta"]*pft
    minute_result = None
    if projected_minutes is not None:
        valid = [r for r in starters if isinstance(r.get("minutes"), (int,float)) and r["minutes"] >= 2]
        if len(valid) >= 3:
            volumes = {k: weighted([r.get("q1_shots", {}).get(k,0)/r["minutes"]
                                    for r in valid[:8]])*projected_minutes for k in keys}
            minute_result = 2*volumes["2pa"]*p2 + 3*volumes["3pa"]*p3 + volumes["fta"]*pft
    result = {
        "shot_volume": baseline_shot,
        "shot_half_shrink": (baseline_shot+baseline)/2,
        "shot_starter": starter_result,
        "shot_starter_half_shrink": (starter_result+baseline)/2 if starter_result is not None else None,
        "shot_minutes": minute_result,
        "shot_minutes_half_shrink": (minute_result+baseline)/2 if minute_result is not None else None,
        "shot_team_context": baseline_shot*(team_scale if team_scale is not None else 1.0),
    }
    return {k: round(max(0.0,v),3) if v is not None else None for k,v in result.items()}
