"""Reproducible, explicitly synthetic baseline, ablation and sensitivity study.

No human subject, GitHub team, or industrial deployment evidence is invented.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from commitlens.config import Config
from commitlens.metrics import run_all
from commitlens.metrics.base import Context, percentile
from commitlens.metrics.size import run as size_run
from commitlens.metrics.trend import run as trend_run
from commitlens.model import Commit, FileChange

SEED = 5351


def confusion(pred: list[bool], label: list[bool]) -> dict:
    if len(pred) != len(label):
        raise ValueError("Prediction-label size mismatch")
    tp = sum(p and y for p, y in zip(pred, label))
    fp = sum(p and not y for p, y in zip(pred, label))
    fn = sum(not p and y for p, y in zip(pred, label))
    tn = sum(not p and not y for p, y in zip(pred, label))
    safe = lambda n, d: round(n / d, 4) if d else None
    return {"TP": tp, "FP": fp, "TN": tn, "FN": fn,
            "precision": safe(tp, tp + fp), "recall": safe(tp, tp + fn),
            "fpr": safe(fp, fp + tn)}


def outlier_experiment(seed: int = SEED) -> dict:
    rng = random.Random(seed)
    baseline_fixed_800, fixed_150, relative, labels = [], [], [], []
    now = datetime.now(timezone.utc)
    per_batch = []
    for batch in range(40):
        # 20 positive batches followed by 20 negative batches, each with
        # exactly thirty commits. The ground truth is construction metadata.
        positive = batch < 20
        outlier_index = rng.randrange(30) if positive else -1
        churns = [rng.randint(10, 50) if j != outlier_index else rng.randint(250, 650)
                  for j in range(30)]
        p95 = percentile([float(x) for x in churns], 95)
        med = percentile([float(x) for x in churns], 50)
        for index, churn in enumerate(churns):
            labels.append(index == outlier_index)
            baseline_fixed_800.append(churn >= 800)
            fixed_150.append(churn >= 150)
            relative.append(churn >= 800 or (churn > p95 and churn >= max(100, 3 * med)))
        per_batch.append({"batch": batch, "positive": positive,
                          "p95": round(p95, 1), "injected_index": outlier_index})
    return {"data": "synthetic, manually seeded; not independently annotated",
            "seed": seed, "batches": 40, "commits_per_batch": 30,
            "injected_positive_records": sum(labels),
            "baselines": {"fixed_lines_800": confusion(baseline_fixed_800, labels),
                          "fixed_lines_150": confusion(fixed_150, labels),
                          "hybrid_p95": confusion(relative, labels)},
            "batch_provenance": per_batch}


def trend_experiment(seed: int = SEED) -> dict:
    rng = random.Random(seed + 1)
    labels, fixed, combined = [], [], []
    for scenario in range(30):
        true_decline = scenario < 15
        counts = [rng.randint(9, 12) for _ in range(8)]
        counts += ([6, 4, 3, 1] if true_decline else [rng.randint(9, 12) for _ in range(4)])
        labels.append(true_decline)
        prev = sum(counts[-8:-4]) / 4
        recent = sum(counts[-4:]) / 4
        fixed.append(recent <= prev * .75)

        commits = []
        start = datetime.now(timezone.utc) - timedelta(weeks=12)
        for week, n in enumerate(counts):
            for k in range(n):
                commits.append(Commit(
                    sha=f"{scenario * 1000 + week * 20 + k:040x}",
                    author="Synthetic", email="synthetic@example.invalid",
                    date=start + timedelta(weeks=week, days=k % 5),
                    subject="feat: synthetic scenario", files=[FileChange("a.py", 1, 1)],
                ))
        ctx = Context(commits, Config(), "synthetic", datetime.now(timezone.utc))
        result = trend_run(ctx)[0]
        combined.append(result.severity.value in ("warn", "alert"))
    return {"data": "synthetic weekly sequences with construction labels",
            "seed": seed + 1, "scenarios": 30,
            "baselines": {"fixed_mean_drop_25pct": confusion(fixed, labels),
                          "mann_kendall_plus_drop": confusion(combined, labels)}}


def sensitivity(seed: int = SEED) -> dict:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    commits = []
    for i in range(150):
        c = 500 if i % 23 == 0 else rng.randint(10, 40)
        commits.append(Commit(f"{i:040x}", f"Synthetic-{i % 5}", f"dev{i % 5}@invalid.test",
                              now - timedelta(days=i % 90), "feat: update test fixture",
                              files=[FileChange(f"src/mod{i % 8}.py", c, 0)]))
    commits.sort(key=lambda x: x.date)
    return {
        "size_percentile": {str(q): size_run(Context(commits, Config({"size": {"giant_percentile": q,
                                              "max_lines": 800}}), "synthetic", now))[1].stats["count"]
                            for q in [80, 90, 95, 99, 100]},
        "ownership_share": {str(q): next(r for r in run_all(Context(
            commits, Config({"ownership": {"single_owner_share": q}}), "synthetic", now))
            if r.check == "ownership").stats["single_owner_files"]
                            for q in [.6, .8, .9]},
        "dormant_days": {str(d): len(next(r for r in run_all(Context(
            commits, Config({"cadence": {"dormant_days": d}}), "synthetic", now))
            if r.check == "cadence_dormant").stats["dormant"])
                         for d in [14, 21, 35]},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "evaluation.json")
    args = ap.parse_args()
    external = {name: {"installed": shutil.which(name) is not None,
                       "assessment": "not a matched labeled-anomaly decision; no precision claim"}
                for name in ("gitinspector", "git-quick-stats")}
    result = {
        "contract": "docs/evaluation_contract.md version 1.0",
        "synthetic_only": True,
        "external_baselines": external,
        "outliers": outlier_experiment(),
        "trend": trend_experiment(),
        "threshold_sensitivity": sensitivity(),
        "human_validation": "NOT CONDUCTED — no reviewers, timing or independent truth",
        "limitations": [
            "Ground truth is construction metadata, not independently audited reality",
            "Fixed 150-line baseline intentionally tests a stronger threshold",
            "Synthetic evaluation does not establish usability or industrial efficacy",
            "No real failure labels or real developer study have been supplied",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for section in ("outliers", "trend"):
        print(section, {k: {metric: v[metric] for metric in ("precision", "recall", "fpr")}
                        for k, v in result[section]["baselines"].items()})
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
