"""Year-separated NBA Q1 model transfer test, 2025 fit -> 2026 evaluation.

Independent player/boxscore features are computed from prior games in each
season. Target starting status is ORACLE historical availability; odds unknown.
All selection is performed exclusively on the earlier 2025 season.
"""
from __future__ import annotations

import csv
import json
import math
import statistics
from pathlib import Path

from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor, ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import HuberRegressor
from sklearn.pipeline import make_pipeline

SRC = Path("research_results")
FEATURES = [
    "baseline","prior_n","weighted_median","median_recent6","rolling_mean",
    "starter_conditional","minute_rate","minute_half_shrink","starter_median",
    "defense_shrink","defense_full","median_half_shrink","median_quarter_shrink",
    "shot_volume","shot_half_shrink","shot_starter","shot_starter_half_shrink",
    "shot_minutes","shot_minutes_half_shrink","shot_team_context",
]


def read_data(file):
    with file.open(newline="",encoding="utf-8") as handle:
        result=list(csv.DictReader(handle))
    for row in result:
        for key in FEATURES+["actual"]:
            try:row[key]=float(row.get(key))
            except (TypeError, ValueError):row[key]=float("nan")
    return result


def xdata(rows):
    return [[r[x] for x in FEATURES] for r in rows]


def model_list():
    return {
      "shallow_quantile_boost":lambda:make_pipeline(
         SimpleImputer(strategy="median"),
         HistGradientBoostingRegressor(loss="absolute_error",max_iter=90,
          max_leaf_nodes=7, min_samples_leaf=55,l2_regularization=25,
          learning_rate=0.04,early_stopping=False,random_state=51)),
      "pooled_huber":lambda:make_pipeline(
         SimpleImputer(strategy="median"),
         HuberRegressor(epsilon=1.5,alpha=15,max_iter=2000)),
      "bagged_trees":lambda:make_pipeline(
         SimpleImputer(strategy="median"),
         RandomForestRegressor(n_estimators=160,max_depth=5,
          min_samples_leaf=35,max_features=0.8,n_jobs=2,random_state=51)),
      "randomized_trees":lambda:make_pipeline(
         SimpleImputer(strategy="median"),
         ExtraTreesRegressor(n_estimators=160,max_depth=5,
          min_samples_leaf=35,max_features=0.8,n_jobs=2,random_state=51)),
    }


def compare(rows, prediction):
    base=statistics.mean(abs(r["actual"]-r["baseline"]) for r in rows)
    candidate=statistics.mean(abs(r["actual"]-v) for r,v in zip(rows,prediction))
    return {"n":len(rows),"baseline_mae":round(base,4),
            "challenger_mae":round(candidate,4),
            "mae_improvement_pct":round(100*(base-candidate)/base,2)}


def main():
    old=read_data(SRC/"opportunity-v3-2025.csv")
    future=read_data(SRC/"opportunity-v3.csv")
    audit_rows=[]
    report={"design":"2025-only model selection/refitting, test on 2026 frozen predictions",
            "limitation":"historical target starters are ORACLE and 2026 results already inspected; not clean prospective performance",
            "promotion_authorized":False,"market_results":[]}
    for metric in ("points","rebounds","assists"):
        train=[r for r in old if r["metric"]==metric and r["date"]<="2025-11-02"]
        valid=[r for r in old if r["metric"]==metric and "2025-11-03"<=r["date"]<="2025-11-06"]
        all_prev=[r for r in old if r["metric"]==metric]
        current=[r for r in future if r["metric"]==metric]
        later=[r for r in current if r["date"]>="2026-11-07"]
        models=[]
        for name,factory in model_list().items():
            model=factory()
            model.fit(xdata(train),[r["actual"] for r in train])
            vv=[max(0,float(p)) for p in model.predict(xdata(valid))]
            score=compare(valid,vv)
            models.append({"name":name,"validation":score})
        chosen=max(models,key=lambda v:v["validation"]["mae_improvement_pct"])
        frozen=model_list()[chosen["name"]]()
        frozen.fit(xdata(all_prev),[r["actual"] for r in all_prev])
        predicted=[max(0,float(p)) for p in frozen.predict(xdata(current))]
        for r,pred in zip(current,predicted):
            audit_rows.append({
                "date": r["date"], "game_id": r["game_id"],
                "player_id": r["player_id"], "metric":metric,
                "baseline":round(r["baseline"],4), "challenger":round(pred,4),
                "actual":r["actual"],
                "baseline_ae":round(abs(r["baseline"]-r["actual"]),4),
                "challenger_ae":round(abs(pred-r["actual"]),4),
            })
        forecast={r["game_id"]+":"+r["player_id"]+":"+r["metric"]:p
                  for r,p in zip(current,predicted)}
        later_preds=[forecast[r["game_id"]+":"+r["player_id"]+":"+r["metric"]] for r in later]
        report["market_results"].append({
            "metric":metric,"earlier_train_n":len(train),"earlier_validation_n":len(valid),
            "year_2026_n":len(current),"year_2026_later_n":len(later),
            "selected_solely_2025":chosen,
            "year_2026_all":compare(current,predicted),
            "year_2026_later":compare(later,later_preds),
            "all_models_2025_validation":models,
        })
    path=SRC/"year_transfer-v1.json"
    path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    with (SRC/"year_transfer-v1.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=["date","game_id","player_id","metric","baseline","challenger","actual","baseline_ae","challenger_ae"])
        writer.writeheader()
        writer.writerows(audit_rows)
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
