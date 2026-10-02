import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
INK, MUTED, GRID, SURFACE = "#2c2c2c", "#666666", "#e6e6e6", "#ffffff"
LOUD, SILENT = "#2a78d6", "#eb6834"
rows = [("Wrong key, first 20 tickets", 57.5, 58.3), ("Jev down, tickets 20 to 39", 57.9, 70.8),
        ("Wrong key, last 20 tickets", 48.8, 63.3), ("Jev 401 from ticket 30 on", 40.0, 61.7),
        ("Tool 500 on 25% of calls", 26.7, 50.0)]
mean, sd = 83.0, 1.9
fig, ax = plt.subplots(figsize=(8.2, 3.3), dpi=200)
fig.patch.set_facecolor(SURFACE)
for s_ in ("top", "right", "left"):
    ax.spines[s_].set_visible(False)
ax.spines["bottom"].set_color(GRID)
ax.tick_params(colors=MUTED, labelsize=10, length=0)
ax.grid(axis="x", color=GRID, linewidth=0.8); ax.set_axisbelow(True)
ax.axvspan(mean - 2 * sd, mean + 2 * sd, color="#eef3fb", zorder=0)
ax.axvline(mean, color="#9bb9e6", linewidth=1)
ax.text(mean, len(rows) - 0.45, "clean runs", color=MUTED, fontsize=9, ha="center", va="bottom")
for y, (name, loud, silent) in enumerate(rows):
    ax.plot([loud, silent], [y, y], color="#c9c9c9", linewidth=2, zorder=1)
    ax.scatter([loud], [y], s=60, color=LOUD, zorder=3, edgecolors=SURFACE, linewidths=1.5, marker="o")
    ax.scatter([silent], [y], s=60, color=SILENT, zorder=3, edgecolors=SURFACE, linewidths=1.5, marker="s")
    if silent - loud > 3:
        ax.text((loud + silent) / 2, y + 0.18, f"+{silent - loud:.0f} pts hidden", color=MUTED, fontsize=8.5, ha="center")
ax.set_yticks(range(len(rows))); ax.set_yticklabels([r[0] for r in rows], color=INK)
ax.set_ylim(-0.6, len(rows) - 0.1); ax.set_xlim(20, 90)
ax.set_xlabel("Plain score (% of 60 tickets correct)", color=MUTED)
h = [plt.Line2D([], [], ls="", marker="o", color=LOUD, markersize=7, label="loud: failed call raises"),
     plt.Line2D([], [], ls="", marker="s", color=SILENT, markersize=7, label="silent: failed call falls back")]
ax.legend(handles=h, loc="lower left", bbox_to_anchor=(0, 1.02), ncol=2, frameon=False, fontsize=9)
fig.tight_layout()
fig.savefig("figs/fig-silent.png", facecolor=SURFACE)
