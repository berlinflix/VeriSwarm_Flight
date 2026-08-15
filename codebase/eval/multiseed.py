"""
Statistical-rigor pass (paper Section 4): re-run the seed-dependent sweeps over
many independent seeds and report mean +/- 95% confidence interval, so the
false-positive rate, the stealth-detection curve, and the ROC come with
confidence bands rather than resting on a single seed.

It reuses the exact experiment functions from eval.run_all (no reimplementation),
calling each under a fresh RNG seed and aggregating their returned rows. Writes
results/false_positive_ci.csv, results/stealth_ci.csv, results/theta_roc_ci.csv.

Run:  python3 -m eval.multiseed
"""

from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict

from eval import harness as H
from eval.run_all import (
    run_r8_false_positive,
    run_rroc_theta,
    run_rst_stealth,
)


def ci95(vals):
    """Mean and half-width of the 95% confidence interval (normal approx)."""
    m = statistics.fmean(vals)
    if len(vals) < 2:
        return m, 0.0
    return m, 1.96 * statistics.stdev(vals) / math.sqrt(len(vals))


def main(n_seeds: int = 20):
    log = H.RunLogger(tag="multiseed")
    seeds = [H.DEFAULT_SEED + 101 * i for i in range(n_seeds)]
    seeds_small = seeds[:10]  # ROC/stealth populations are larger per seed

    # --- false-positive rate, with vs without the co-visibility gate ---
    fp = defaultdict(list)
    for s in seeds:
        rows = run_r8_false_positive(log, random.Random(s), 0)[0].rows
        for r in rows:
            fp[r["condition"]].append(r["fp_rate"])
    fp_rows = []
    for cond, vals in fp.items():
        m, h = ci95(vals)
        fp_rows.append({"condition": cond, "n_seeds": len(vals),
                        "trials_per_seed": 200,
                        "fp_rate_mean": round(m, 4), "fp_rate_ci95": round(h, 4),
                        "fp_rate_min": round(min(vals), 4), "fp_rate_max": round(max(vals), 4)})
    H.write_csv("false_positive_ci.csv", fp_rows)

    # --- stealth: detection rate vs adversarial magnitude ---
    st = defaultdict(list)
    for s in seeds_small:
        rows = run_rst_stealth(log, random.Random(s), 0)[0].rows
        for r in rows:
            st[r["l2_magnitude"]].append(r["detection_rate"])
    st_rows = []
    for mag in sorted(st):
        m, h = ci95(st[mag])
        st_rows.append({"l2_magnitude": mag, "theta_ref": 0.5, "n_seeds": len(st[mag]),
                        "detection_rate_mean": round(m, 4), "detection_rate_ci95": round(h, 4)})
    H.write_csv("stealth_ci.csv", st_rows)

    # --- ROC: tpr/fpr vs theta ---
    roc = defaultdict(lambda: {"tpr": [], "fpr": []})
    for s in seeds_small:
        rows = run_rroc_theta(log, random.Random(s), 0)[0].rows
        for r in rows:
            roc[r["theta"]]["tpr"].append(r["tpr"])
            roc[r["theta"]]["fpr"].append(r["fpr"])
    roc_rows = []
    for theta in sorted(roc):
        tm, th = ci95(roc[theta]["tpr"])
        fm, fh = ci95(roc[theta]["fpr"])
        roc_rows.append({"theta": theta, "n_seeds": len(roc[theta]["tpr"]),
                         "tpr_mean": round(tm, 4), "tpr_ci95": round(th, 4),
                         "fpr_mean": round(fm, 4), "fpr_ci95": round(fh, 4)})
    H.write_csv("theta_roc_ci.csv", roc_rows)

    log.close()
    print("false-positive (mean +/- 95% CI over", n_seeds, "seeds):")
    for r in fp_rows:
        print(f"  {r['condition']:24} {r['fp_rate_mean']*100:6.2f}% +/- {r['fp_rate_ci95']*100:.2f}%"
              f"  [min {r['fp_rate_min']*100:.1f}%, max {r['fp_rate_max']*100:.1f}%]")
    # operating point theta=0.5
    op = next((r for r in roc_rows if abs(r["theta"] - 0.5) < 1e-9), None)
    if op:
        print(f"ROC operating point theta=0.5: TPR {op['tpr_mean']:.3f} +/- {op['tpr_ci95']:.3f}, "
              f"FPR {op['fpr_mean']:.3f} +/- {op['fpr_ci95']:.3f}")
    print("wrote results/false_positive_ci.csv, stealth_ci.csv, theta_roc_ci.csv")


if __name__ == "__main__":
    main()
