"""Pre-registered, forward-only quality gate for experimental NBA Q1 shot model.

Passing this diagnostic does NOT prove profitable betting: genuine market
lines, odds, vig, availability and realized ROI must be tested separately.
"""
from __future__ import annotations

import random
from collections import defaultdict


def validate_shot_shadow(rows: list[dict], bootstrap_n: int = 2000) -> dict:
    paired = [r for r in rows if r.get("sport") == "nba" and r.get("metric") == "points"
              and r.get("shot_absolute_error") is not None]
    groups = defaultdict(list)
    dates = set()
    for row in paired:
        key = str(row["game_id"])
        groups[key].append(float(row["absolute_error"]) - float(row["shot_absolute_error"]))
        if row.get("date_et"):
            dates.add(row["date_et"])
    n = len(paired)
    if not n:
        return {
            "status": "collecting", "paired_n":0, "game_count":0, "dates":0,
            "accuracy_gate_passed":False,
            "profitable_betting_proven":False,
            "reason":"No verified future games with both estimates graded yet.",
        }
    delta = sum(v for group in groups.values() for v in group) / n
    baseline = sum(float(r["absolute_error"]) for r in paired) / n
    ids = sorted(groups)
    rng = random.Random(443)
    sims = []
    for _ in range(bootstrap_n):
        selected = [groups[ids[rng.randrange(len(ids))]] for _ in ids]
        sims.append(sum(v for group in selected for v in group)
                    / sum(len(group) for group in selected))
    sims.sort()
    ci_low = sims[int(0.025 * bootstrap_n)]
    ci_high = sims[min(bootstrap_n-1,int(0.975*bootstrap_n))]
    relative = 100*delta/baseline if baseline else 0.0
    # Conservative gate. Passing means evidence of projection accuracy only.
    enough = n>=500 and len(ids)>=50 and len(dates)>=14
    passed = enough and relative>=8 and ci_low>0
    return {
        "status": "accuracy_gate_passed" if passed else "evaluating",
        "paired_n":n,"game_count":len(ids),"dates":len(dates),
        "baseline_mae":round(baseline,4),
        "mae_improvement":round(delta,4),
        "relative_improvement_pct":round(relative,2),
        "game_cluster_ci95_delta": [round(ci_low,4),round(ci_high,4)],
        "minimum_samples_met":enough,
        "accuracy_gate_passed":passed,
        "profitable_betting_proven":False,
        "promotion_automatic":False,
        "requirements":"At least 500 valid paired projections, 50 games, 14 dates, >=8% paired MAE improvement and game-cluster lower CI above zero. Betting lines, odds, vig and net ROI separately required.",
    }
