"""Plot one Sewall.ai flood sensor run: NWS gauge categories per area and tide stations against flood thresholds.

Usage: python3 scripts/plot_flood.py OUT.png MANIFEST

Both panels use only the recorded flood_sensors observations in the manifest.
Tide station levels are shown as feet relative to each station's own minor
flood threshold, because CO-OPS station datums differ from station to station.
"""

import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Okabe-Ito palette, ordered from no flooding to major.
CATEGORIES = [("no_flooding", "No flooding", "#009E73"), ("action", "Action", "#F0E442"),
              ("minor", "Minor", "#E69F00"), ("moderate", "Moderate", "#D55E00"), ("major", "Major", "#CC79A7"),
              ("not_defined", "No thresholds set", "#BBBBBB"), ("out_of_service", "Out of service or not current", "#666666")]
NOT_CURRENT = {"out_of_service", "obs_not_current"}


def observations(path):
    with open(path) as handle:
        manifest = json.load(handle)
    found = [item["observation"] for item in manifest["actions"]
             if item.get("status") == "completed" and (item.get("observation") or {}).get("operation") == "flood_sensors"]
    return found, manifest["content_digest"]


def main(out, path):
    found, digest = observations(path)
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4.4), gridspec_kw={"width_ratios": [1.15, 1]})
    areas = [obs["area"] for obs in found]
    starts = [0] * len(found)
    for key, name, color in CATEGORIES:
        counts = []
        for obs in found:
            tally = obs["nwps_categories"]
            counts.append(sum(n for k, n in tally.items() if k in NOT_CURRENT) if key == "out_of_service"
                          else tally.get(key, 0))
        if any(counts):
            left.barh(areas, counts, left=starts, color=color, edgecolor="white", label=name)
            starts = [a + b for a, b in zip(starts, counts)]
    left.invert_yaxis()
    left.set_xlabel("NWS gauges (NWPS) by observed flood category")
    left.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, frameon=False)
    left.grid(axis="x", alpha=0.3)

    stations = [(s["name"], s["value"] - s["thresholds"]["minor"], obs["area"])
                for obs in found for s in obs["coops_stations"]
                if s.get("value") is not None and s["thresholds"].get("minor") is not None]
    names = [f"{name} ({area})" for name, _, area in stations]
    right.barh(names, [delta for _, delta, _ in stations], color="#0072B2")
    right.axvline(0, color="#D55E00", lw=1.5)
    right.text(0.05, -0.6, "minor flood\nthreshold", color="#D55E00", fontsize=7, va="top")
    right.invert_yaxis()
    right.set_xlabel("NOAA tide station level minus\nits minor flood threshold (ft)")
    right.grid(axis="x", alpha=0.3)
    right.tick_params(axis="y", labelsize=8)
    when = found[0]["retrieved_at"].replace("T", " ").rstrip("Z") if found else "?"
    fig.suptitle(f"Live Chesapeake flood sensors at {when} UTC, from a replayable Sewall.ai run (digest {digest[:8]})",
                 fontsize=10)
    fig.tight_layout()
    fig.savefig(out, dpi=200)
    print(f"{path}: {len(found)} areas, {len(stations)} tide stations, digest {digest[:12]}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
