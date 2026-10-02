"""Paper figures from the analysis result files.

  python analysis/make_figures.py --out <site>/writing/run-valid

Figure 2: every dev and held-out run, plain score against injected cause,
          marked by the gate's verdict, with the clean noise band.
Figure 3: distance from the clean mean, plain score against score on valid
          items, for runs with item-level faults.
"""

import argparse
import hashlib
import json
import os
import statistics

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
INK, MUTED, GRID, SURFACE = "#2c2c2c", "#666666", "#e6e6e6", "#ffffff"
VERDICT = {  # status colors, each with its own marker so color is never the only cue
    "valid": ("#0ca30c", "o", "valid"),
    "degraded": ("#e0a000", "D", "degraded"),
    "invalid": ("#d03b3b", "X", "invalid"),
}
ROWS = [("clean", "Clean"), ("control", "Control (declared change)"), ("infrastructure", "Infrastructure"),
        ("config_drift", "Config drift"), ("task_defect", "Task or dataset defect"),
        ("harness_bug", "Harness or scorer bug"), ("judge_failure", "Judge failure")]
SERIES = "#2a78d6"


def style(ax):
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=10, length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def jitter(key, spread=0.28):
    h = int(hashlib.sha256(key.encode()).hexdigest()[:6], 16) / 16 ** 6  # stable across runs
    return (h - 0.5) * 2 * spread


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", default=os.path.join(HERE, "results_dev_final.json"))
    ap.add_argument("--heldout", default=os.path.join(HERE, "results_heldout_fixed.json"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    dev, held = json.load(open(a.dev)), json.load(open(a.heldout))
    rows = dev["runs_detail"] + held["runs_detail"]
    noise = held["noise_floor"]
    mean, sd = noise["mean"] * 100, noise["sd"] * 100
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})

    # ---------------- Figure 2
    fig, ax = plt.subplots(figsize=(8.2, 4.4), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    style(ax)
    ax.axvspan(mean - 2 * sd, mean + 2 * sd, color="#eef3fb", zorder=0)
    ax.axvline(mean, color="#9bb9e6", linewidth=1, zorder=1)
    ax.text(mean, len(ROWS) - 0.35, f"clean runs: {mean:.1f}% ± 2 sd", color=MUTED, fontsize=9, ha="center", va="bottom")
    stopped = 0
    for y, (cat, _) in enumerate(reversed(ROWS)):
        for r in rows:
            if r["category"] != cat:
                continue
            if r["naive"] is None:
                stopped += 1
                continue
            color, marker, _ = VERDICT[r["gate"]]
            ax.scatter(r["naive"] * 100, y + jitter(r["run"]), s=26, color=color, marker=marker,
                       edgecolors=SURFACE, linewidths=0.8, zorder=3)
    ax.set_yticks(range(len(ROWS)))
    ax.set_yticklabels([label for _, label in reversed(ROWS)], color=INK)
    ax.set_xlabel("Plain score (% of 60 tickets correct)", color=MUTED)
    ax.set_xlim(left=max(0, min(r["naive"] for r in rows if r["naive"] is not None) * 100 - 3), right=95)
    ax.set_ylim(-0.6, len(ROWS) - 0.1)
    handles = [plt.Line2D([], [], linestyle="", marker=m, color=c, markersize=7, markeredgecolor=SURFACE, label=l)
               for c, m, l in VERDICT.values()]
    ax.legend(handles=handles, title="Gate verdict", loc="lower left", frameon=False, fontsize=9, title_fontsize=9,
              bbox_to_anchor=(0.0, 1.02), ncol=3, alignment="left")
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "fig-scores-by-verdict.png"), facecolor=SURFACE)
    plt.close(fig)

    # ---------------- Figure 3
    item_level = [r for r in rows if r["category"] in ("infrastructure", "judge_failure", "task_defect", "harness_bug")
                  and not (r["plan"] or "").startswith("duplicate_item") and r["naive"] is not None
                  and r["valid_items"] is not None]
    plain = [(r["naive"] * 100 - mean) for r in item_level]
    valid = [(r["valid_items"] * 100 - mean) for r in item_level]
    fig, ax = plt.subplots(figsize=(8.2, 2.9), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    style(ax)
    ax.axvspan(-2 * sd, 2 * sd, color="#eef3fb", zorder=0)
    ax.axvline(0, color="#9bb9e6", linewidth=1, zorder=1)
    for y, (vals, label, runs) in enumerate([(valid, "Score on valid items", item_level),
                                             (plain, "Plain score", item_level)]):
        for v, r in zip(vals, runs):
            ax.scatter(v, y + jitter(r["run"] + label, 0.22), s=18, color=SERIES, alpha=0.75,
                       edgecolors=SURFACE, linewidths=0.6, zorder=3)
        med = statistics.median(vals)
        ax.plot([med, med], [y - 0.32, y + 0.32], color=INK, linewidth=2, zorder=4)
        ax.text(med, y + 0.36, f"median {med:+.1f}", color=INK, fontsize=9, ha="center", va="bottom")
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["Score on valid items", "Plain score"], color=INK)
    ax.set_ylim(-0.55, 1.75)
    ax.set_xlabel("Points from the clean mean (shaded: clean runs ± 2 sd)", color=MUTED)
    fig.tight_layout()
    fig.savefig(os.path.join(a.out, "fig-recovery.png"), facecolor=SURFACE)
    plt.close(fig)

    numbers = {"runs_plotted": len(rows) - stopped, "stopped_at_preflight": stopped,
               "item_level_runs": len(item_level),
               "median_gap_plain": statistics.median(plain), "median_gap_valid": statistics.median(valid),
               "noise_floor": noise}
    json.dump(numbers, open(os.path.join(a.out, "figure_numbers.json"), "w"), indent=1)
    print(json.dumps(numbers, indent=1))


if __name__ == "__main__":
    main()
