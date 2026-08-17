"""
Generate the paper's figures (Fig 9-14) from the results CSVs.

Run after the experiments:  python3 -m eval.make_figures
Writes PNGs to  figures/  in the repo root. Each figure is drawn only if its
CSV exists and is filled; missing/pending ones are skipped with a note (e.g.
the OP-TEE signing latency must be synced back from the Jetson first).

Style is intentionally plain and monochrome-friendly for an IEEE two-column
layout: small figures, single accent, light grid.
"""

from __future__ import annotations

import csv
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
FIGS = REPO / "figures"

# IEEE house style: Times New Roman throughout, grayscale-safe, inward ticks,
# no colour accent. Series are separated by marker and line style, not hue, so
# the figures survive a black-and-white print.
INK = "#000000"
ACCENT = "#000000"
MUTED = "#7a7a7a"
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Nimbus Roman", "Liberation Serif"],
    "mathtext.fontset": "stix",
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "axes.edgecolor": INK,
    "axes.labelcolor": INK,
    "axes.linewidth": 0.8,
    "xtick.color": INK,
    "ytick.color": INK,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.major.size": 3.5,
    "ytick.major.size": 3.5,
    "xtick.minor.visible": True,
    "ytick.minor.visible": True,
    "xtick.minor.size": 2.0,
    "ytick.minor.size": 2.0,
    "xtick.top": True,
    "ytick.right": True,
    "grid.color": "#b0b0b0",
    "grid.linewidth": 0.4,
    "grid.linestyle": ":",
    "legend.frameon": False,
    "figure.dpi": 300,
    "savefig.dpi": 300,
})


def _read(name):
    path = RESULTS / name
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open() as fh:
        rows = list(csv.DictReader(fh))
    return rows or None


def _save(fig, name):
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(FIGS / name, bbox_inches="tight")
    fig.savefig((FIGS / name).with_suffix(".pdf"), bbox_inches="tight")
    plt.close(fig)
    print("wrote", (FIGS / name).relative_to(REPO))


def _skip(name, why):
    print(f"skip {name}: {why}")


def fig09_consensus_latency():
    rows = _read("consensus_latency.csv")
    if not rows:
        return _skip("fig09", "consensus_latency.csv missing")
    n = [int(r["N"]) for r in rows]
    ms = [float(r.get("local_verify_and_tally_ms_mean", r.get("decision_ms_mean")))
          for r in rows]
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.plot(n, ms, marker="o", color=ACCENT, lw=1.6)
    ax.set_xlabel("Swarm size $N$")
    ax.set_ylabel("Consensus latency (ms)")
    ax.set_xticks(n)
    ax.grid(True)
    _save(fig, "fig09_consensus_latency.png")


def fig10_drop_sweep():
    rows = _read("drop_sweep.csv")
    if not rows:
        return _skip("fig10", "drop_sweep.csv missing")
    pct = [float(r["drop_pct"]) for r in rows]
    ms = [float(r["detect_ms_mean"]) for r in rows]
    std = [float(r["detect_ms_std"]) for r in rows]
    rate = [float(r["decision_rate"]) * 100 for r in rows]
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.errorbar(pct, ms, yerr=std, marker="o", ms=3.5, mfc="white", color=INK,
                lw=1.1, capsize=2, label="Detection latency")
    ax.set_xlabel("Message-drop rate (%)")
    ax.set_ylabel("Detection latency (ms)")
    ax2 = ax.twinx()
    ax2.plot(pct, rate, marker="s", ms=3.5, mfc=INK, color=INK, lw=1.1, ls="--",
             label="Decision reached")
    ax2.set_ylabel("Decision reached (%)")
    ax2.tick_params(direction="in")
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="center left", fontsize=7)
    ax2.set_ylim(0, 105)
    ax.grid(True)
    _save(fig, "fig10_drop_sweep.png")


def fig11_overhead():
    rows = _read("overhead.csv")
    if not rows:
        return _skip("fig11", "overhead.csv missing")
    n = [r["N"] for r in rows]
    by = [int(r.get("signed_payload_bytes_per_cycle", r.get("bytes_per_cycle")))
          for r in rows]
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    ax.bar(n, by, facecolor="white", edgecolor=INK, linewidth=0.8, width=0.55)
    ax.set_xlabel("Swarm size $N$")
    ax.set_ylabel("Bytes per cycle")
    for x, y in zip(n, by):
        ax.text(x, y, str(y), ha="center", va="bottom", fontsize=7)
    ax.grid(True, axis="y")
    _save(fig, "fig11_overhead.png")


def fig12_sign_latency():
    sw = _read("sign_latency.csv")
    hw = _read("sign_latency_optee.csv")
    if not sw:
        return _skip("fig12", "sign_latency.csv missing")
    sw_ms = float(sw[0]["sign_ms_mean"])
    labels, vals = ["Software\n(PyNaCl)"], [sw_ms]
    if hw and str(hw[0].get("sign_ms_mean")) not in ("PENDING", "", "None"):
        labels.append("Hardware\n(OP-TEE)")
        vals.append(float(hw[0]["sign_ms_mean"]))
    else:
        print("note fig12: OP-TEE row pending — sync sign_latency_optee.csv from the Jetson")
    fig, ax = plt.subplots(figsize=(3.0, 2.4))
    bars = ax.bar(labels, vals, facecolor="white", edgecolor=INK, linewidth=0.8, width=0.5)
    for b, hatch in zip(bars, ("", "///")):
        b.set_hatch(hatch)
    ax.set_ylabel("Signing latency (ms, log)")
    ax.set_yscale("log")
    for i, v in enumerate(vals):
        ax.text(i, v, f"{v:.3f}", ha="center", va="bottom", fontsize=7)
    ax.grid(True, axis="y")
    _save(fig, "fig12_sign_latency.png")


def fig13_roc():
    rows = _read("theta_roc.csv")
    if not rows:
        return _skip("fig13", "theta_roc.csv missing")
    fpr = [float(r["fpr"]) for r in rows]
    tpr = [float(r["tpr"]) for r in rows]
    theta = [float(r["theta"]) for r in rows]
    fig, ax = plt.subplots(figsize=(3.2, 2.6))
    ax.plot(fpr, tpr, color=INK, lw=1.1)
    # Mark the operating point theta = 0.5.
    op = min(range(len(theta)), key=lambda i: abs(theta[i] - 0.5))
    ax.scatter([fpr[op]], [tpr[op]], facecolor="white", edgecolor=INK,
               linewidth=1.0, zorder=5, s=30)
    ax.annotate(r"$\theta=0.5$", (fpr[op], tpr[op]),
                textcoords="offset points", xytext=(8, -10), fontsize=8)
    ax.set_xlabel("False-positive rate")
    ax.set_ylabel("True-positive rate")
    ax.set_xlim(-0.02, 1.02)
    ax.set_ylim(0, 1.02)
    ax.grid(True)
    _save(fig, "fig13_roc.png")


def fig14_stealth():
    ci = _read("stealth_ci.csv")  # prefer the multi-seed mean +/- 95% CI
    if ci:
        rows = sorted(ci, key=lambda r: float(r["l2_magnitude"]))
        mag = [float(r["l2_magnitude"]) for r in rows]
        det = [float(r["detection_rate_mean"]) * 100 for r in rows]
        err = [float(r["detection_rate_ci95"]) * 100 for r in rows]
        theta = float(rows[0]["theta_ref"])
    else:
        rows = _read("stealth_sweep.csv")
        if not rows:
            return _skip("fig14", "stealth_sweep.csv missing")
        rows = sorted(rows, key=lambda r: float(r["l2_magnitude"]))
        mag = [float(r["l2_magnitude"]) for r in rows]
        det = [float(r["detection_rate"]) * 100 for r in rows]
        err = None
        theta = float(rows[0]["theta_ref"])
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    if err is not None:
        lo = [d - e for d, e in zip(det, err)]
        hi = [d + e for d, e in zip(det, err)]
        ax.fill_between(mag, lo, hi, facecolor="#d9d9d9", edgecolor="none")
    ax.plot(mag, det, marker="o", ms=3.5, mfc="white", color=INK, lw=1.1)
    ax.axvline(theta, color=INK, ls="--", lw=1.0)
    ax.annotate(r"$\theta$", (theta, 100), textcoords="offset points",
                xytext=(5, -3), fontsize=9, va="top")
    ax.set_xlabel(r"Adversarial-patch magnitude $\|\cdot\|_2$")
    ax.set_ylabel("Detection rate (%)")
    ax.set_ylim(-5, 105)
    ax.grid(True)
    _save(fig, "fig14_stealth.png")


def fig_reputation_trace():
    rows = _read("reputation_trace.csv")
    if not rows:
        return _skip("fig_reputation", "reputation_trace.csv missing")
    series: dict = {}
    for r in rows:
        series.setdefault(r["drone_id"], ([], [], r["is_colluder"] == "True"))
        series[r["drone_id"]][0].append(int(r["round"]))
        series[r["drone_id"]][1].append(float(r["r_i"]))
    fig, ax = plt.subplots(figsize=(3.4, 2.4))
    for did, (rounds, ri, is_coll) in series.items():
        ax.plot(rounds, ri, color=(INK if is_coll else MUTED),
                ls=("-" if is_coll else "--"), lw=1.3 if is_coll else 0.8,
                marker=("o" if is_coll else None), ms=3, mfc="white",
                markevery=2, zorder=3 if is_coll else 2)
    ax.set_xlabel("Consensus round")
    ax.set_ylabel("Reputation $r_i$")
    ax.set_ylim(0, 1.05)
    ax.grid(True)
    ax.plot([], [], color=INK, lw=1.3, marker="o", ms=3, mfc="white", label="colluder")
    ax.plot([], [], color=MUTED, ls="--", lw=0.8, label="honest")
    ax.legend(fontsize=7)
    _save(fig, "fig_reputation_trace.png")


def fig_patch_transfer():
    rows = _read("adv_patch_transfer.csv")
    if not rows:
        return _skip("fig_patch_transfer", "adv_patch_transfer.csv missing")
    scene = "bus.jpg"
    rows = [r for r in rows if r["scene"] == scene]
    if not rows:
        return _skip("fig_patch_transfer", f"no {scene} rows")

    def series(variant):
        rs = sorted((r for r in rows if r["variant"] == variant),
                    key=lambda r: float(r["phi_deg"]))
        return ([float(r["peer_patched_conf_mean"]) for r in rs],
                [float(r["peer_patched_conf_std"]) for r in rs])

    sv = sorted((r for r in rows if r["variant"] == "single-view"),
                key=lambda r: float(r["phi_deg"]))
    phi = [float(r["phi_deg"]) for r in sv]
    clean = [float(r["clean_conf_mean"]) for r in sv]
    m_s, sd_s = series("single-view")
    m_r, sd_r = series("viewpoint-robust")

    fig, ax = plt.subplots(figsize=(3.5, 2.7))
    ax.plot(phi, clean, color=MUTED, lw=0.9, ls=":", label="clean scene (control)")
    ax.errorbar(phi, m_s, yerr=sd_s, color=INK, lw=1.1, ls="-", marker="o", ms=3.5,
                mfc="white", capsize=2, label="single-view patch")
    ax.errorbar(phi, m_r, yerr=sd_r, color=INK, lw=1.1, ls="--", marker="s", ms=3.5,
                mfc=INK, capsize=2, label="viewpoint-robust patch")
    ax.axhline(0.25, color=INK, lw=0.7, ls="-.")
    ax.text(0.4, 0.28, "detection threshold", fontsize=7, color=INK)
    for x, lbl in ((12, "3 m"), (23, "6 m")):
        ax.axvline(x, color="#cccccc", lw=0.8)
        ax.text(x - 0.6, 0.97, lbl, fontsize=6.5, color="#666666", rotation=90, va="top", ha="right")
    ax.set_xlabel(r"Peer viewpoint disparity $\varphi$ (deg)")
    ax.set_ylabel("Peer detection conf.\nof hidden object")
    ax.set_ylim(-0.03, 1.0)
    ax.set_xlim(-1, 24)
    ax.grid(True)
    ax.legend(fontsize=7, loc="center left")
    _save(fig, "fig_patch_transfer.png")


def main():
    for fn in (fig09_consensus_latency, fig10_drop_sweep, fig11_overhead,
               fig12_sign_latency, fig13_roc, fig14_stealth, fig_reputation_trace,
               fig_patch_transfer):
        fn()
    print(f"\nFigures in {FIGS}")


if __name__ == "__main__":
    main()
