"""Plot per-day Sentinel-2 NDCI from one or more Sewall.ai run manifests.

Usage: python3 scripts/plot_ndci.py OUT.png MANIFEST [MANIFEST ...]

Each manifest contributes one series built only from its recorded
chlorophyll_timeseries observations. The August to September 2022 window of
the York River bloom simulated by Yu et al. (2025) is shaded for reference.
"""

from datetime import date
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

BLOOM_WINDOW = (date(2022, 8, 1), date(2022, 9, 30))


def series(path):
    with open(path) as handle:
        manifest = json.load(handle)
    label, points = None, {}
    for item in manifest["actions"]:
        obs = item.get("observation") or {}
        if item.get("status") != "completed" or obs.get("operation") != "chlorophyll_timeseries":
            continue
        label = obs["site_label"]
        for scene in obs["scenes"]:
            if scene.get("ndci_mean") is not None:
                points[scene["date"]] = (scene["ndci_mean"], scene["water_pixels"])
    days = sorted(points)
    return label, [date.fromisoformat(d) for d in days], [points[d][0] for d in days], manifest["content_digest"]


def main(out, *paths):
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    ax.axvspan(*BLOOM_WINDOW, color="#f4c27a", alpha=0.35, lw=0,
               label="York River bloom window (Yu et al., 2025)")
    for path in paths:
        label, days, values, digest = series(path)
        ax.plot(days, values, marker="o", ms=4, lw=1.2, label=f"{label} ({len(days)} days)")
        print(f"{path}: {len(days)} days, digest {digest[:12]}")
    ax.set_ylabel("Mean NDCI, clear permanent water")
    ax.set_title("Sentinel-2 chlorophyll proxy, 2022, from replayable Sewall.ai runs", fontsize=10)
    ax.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.28), ncol=1, frameon=False)
    ax.grid(alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(out, dpi=200)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
