"""Supervised first-quarter candidate using only chronologically prior feature rows.

A candidate research exercise, NOT authorized for publication or betting.
Historical target starters were identified from final box scores (oracle).
2026-11-07..12 has already been inspected, so it is exploratory only.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import HuberRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline


FEATURES = [
    "baseline", "prior_n", "weighted_median", "median_recent6",
    "rolling_mean", "starter_conditional", "minute_rate",
    "minute_half_shrink", "starter_median", "defense_shrink",
    "defense_full", "median_half_shrink", "median_quarter_shrink",
]
PATH = Path("research_results/opportunity-v3.csv")


def load_rows():
    with PATH.open(newline="", encoding="utf-8") as handle:
        data = list(csv.DictReader(handle))
    for row in data:
        for key in FEATURES + ["actual", "baseline_ae"]:
            try:
                row[key] = float(row[key])
            except (TypeError, ValueError, KeyError):
                row[key] = float("nan")
    return data


def xdata(rows):
    return [[r[k] for k in FEATURES] for r in rows]


def evaluate(y, baseline, pred):
    ebase = [abs(a-b) for a, b in zip(y, baseline)]
    e_new = [abs(a-b) for a, b in zip(y, pred)]
    b = statistics.mean(ebase)
    n = statistics.mean(e_new)
    return {"n": len(y), "baseline_mae": round(b, 4),
            "candidate_mae": round(n, 4),
            "relative_mae_improvement_pct": round((b-n)*100/b, 2)}


def candidate_factories():
    return {
      "shallow_absolute_boosting": lambda: make_pipeline(
          SimpleImputer(strategy="median", add_indicator=False),
          HistGradientBoostingRegressor(loss="absolute_error", max_iter=95,
              learning_rate=0.04, max_leaf_nodes=5, min_samples_leaf=65,
              l2_regularization=35, early_stopping=False, random_state=19)),
      "robust_linear": lambda: make_pipeline(
          SimpleImputer(strategy="median"),
          HuberRegressor(epsilon=1.35, alpha=10, max_iter=1000)),
      "robust_bagged_forest": lambda: make_pipeline(
          SimpleImputer(strategy="median"),
          RandomForestRegressor(n_estimators=120, max_depth=4,
              min_samples_leaf=45, max_features=0.7, n_jobs=2, random_state=19)),
    }


def main():
    allrows = load_rows()
    output = {"data_source": str(PATH), "rows": len(allrows),
              "note": "All features strictly computed from prior games; target starter identity is oracle/hindsight.",
              "holdout_warning": "2026-11-07 to 11-12 was already inspected by earlier research; no promotion.",
              "markets": [], "automatic_production_promotion": False}
    for metric in ["points", "rebounds", "assists"]:
        data = [r for r in allrows if r["metric"] == metric]
        train = [r for r in data if r["date"] <= "2026-11-02"]
        valid = [r for r in data if "2026-11-03" <= r["date"] <= "2026-11-06"]
        test = [r for r in data if r["date"] >= "2026-11-07"]
        if min(map(len, [train, valid, test])) < 100:
            output["markets"].append({"metric": metric, "error": "insufficient date-based samples"})
            continue
        result = {"metric": metric, "training_n": len(train), "validation_n": len(valid),
                  "diagnostic_n": len(test), "candidates": []}
        for name, factory in candidate_factories().items():
            estimator = factory()
            estimator.fit(xdata(train), [r["actual"] for r in train])
            vv = [max(0.0, float(v)) for v in estimator.predict(xdata(valid))]
            tt = [max(0.0, float(v)) for v in estimator.predict(xdata(test))]
            v = evaluate([r["actual"] for r in valid], [r["baseline"] for r in valid], vv)
            t = evaluate([r["actual"] for r in test], [r["baseline"] for r in test], tt)
            result["candidates"].append({"name": name, "validation": v, "exploratory_test": t})
        selected = max(result["candidates"], key=lambda x: x["validation"]["relative_mae_improvement_pct"])
        result["validation_selected"] = selected["name"]
        result["selected_exploratory_diagnostic"] = selected["exploratory_test"]
        output["markets"].append(result)
    folder = Path("research_results")
    (folder / "meta-v1.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))
    return output


if __name__ == "__main__":
    main()
