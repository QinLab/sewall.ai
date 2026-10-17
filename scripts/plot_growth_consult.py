"""Plot the evidence a growth-model consultation found, from recorded run manifests.

Usage: python3 scripts/plot_growth_consult.py OUT.png NAME=MANIFEST [NAME=MANIFEST ...]

Panel A shows, for each run, the source total of every completed search, grouped
by the model input the lab manager planned it for. Panel B shows GenBank marker
record counts per taxon from the taxon_inventory steps. Every number comes from
a recorded observation; a CSV of the same values is written next to the figure.
"""

import csv
import json
import math
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# Model inputs in display order, each with the test that recognizes its planned step.
INPUTS = (
    ("Organism literature", lambda db, q: db == "pubmed" and " AND " not in q),
    ("Growth responses", lambda db, q: db == "pubmed" and " AND growth AND " in q),
    ("Genotype: ribotype and strain papers", lambda db, q: db == "pubmed" and "ribotype" in q),
    ("Pathways: expression papers", lambda db, q: db == "pubmed" and "gene expression" in q),
    ("Community: interaction papers", lambda db, q: db == "pubmed" and "allelopathy" in q and "growth" not in q),
    ("Pathways: transcriptome projects", lambda db, q: db == "bioproject" and "RNA-seq" in q),
    ("Community: microbiome projects", lambda db, q: db == "bioproject" and "microbiome" in q),
)
MARKERS = ("lsu", "ssu", "its", "coi")
SERIES = ("#2a78d6", "#eb6834")
RAMP = LinearSegmentedColormap.from_list("blue", ["#cde2fb", "#6da7ec", "#256abf", "#0d366b"])
INK, MUTED, EMPTY = "#0b0b0b", "#52514e", "#f0efec"


def load(path):
    with open(path) as handle:
        manifest = json.load(handle)
    totals, inventories = {}, {}
    for item in manifest["actions"]:
        obs, action = item.get("observation") or {}, item["action"]
        if item.get("status") != "completed":
            continue
        if action["action"] == "search":
            for label, test in INPUTS:
                if test(action["database"], action["query"]):
                    totals[label] = obs["total"]
        elif action["action"] == "taxon_inventory":
            inventories[obs["taxon"]] = obs
    return manifest, totals, inventories


def main(out, *pairs):
    runs = [(name, *load(path)) for name, path in (pair.split("=", 1) for pair in pairs)]
    fig, (left, right) = plt.subplots(1, 2, figsize=(11, 4.4), gridspec_kw={"width_ratios": [1.35, 1]})
    peak = max([total for _, _, totals, _ in runs for total in totals.values()] + [1])
    rows = []
    for col, (name, manifest, totals, _) in enumerate(runs):
        for row, (label, _) in enumerate(INPUTS):
            value = totals.get(label)
            if value is None:
                left.add_patch(plt.Rectangle((col, row), 0.96, 0.92, color=EMPTY, lw=0))
                left.text(col + 0.48, row + 0.46, "not in script", ha="center", va="center", fontsize=7.5, color=MUTED)
                continue
            shade = math.log1p(value) / math.log1p(peak)
            left.add_patch(plt.Rectangle((col, row), 0.96, 0.92, color=RAMP(shade), lw=0))
            left.text(col + 0.48, row + 0.46, str(value), ha="center", va="center", fontsize=9,
                      color="#ffffff" if shade > 0.55 else INK)
            rows.append({"run": name, "input": label, "total": value, "manifest_digest": manifest["content_digest"]})
    left.set_xlim(0, len(runs))
    left.set_ylim(len(INPUTS), 0)
    left.set_xticks([i + 0.48 for i in range(len(runs))], [name for name, *_ in runs], fontsize=9)
    left.set_yticks([i + 0.46 for i in range(len(INPUTS))], [label for label, _ in INPUTS], fontsize=9)
    left.tick_params(length=0)
    for spine in left.spines.values():
        spine.set_visible(False)
    left.set_title("A. Source totals for each planned search", loc="left", fontsize=10, color=INK)

    taxa = {}
    for _, _, _, inventories in runs:
        taxa.update(inventories)
    width = 0.8 / max(len(taxa), 1)
    for index, (taxon, obs) in enumerate(sorted(taxa.items())):
        counts = [obs["marker_counts"].get(marker, 0) for marker in MARKERS]
        xs = [i + (index - (len(taxa) - 1) / 2) * width for i in range(len(MARKERS))]
        right.bar(xs, counts, width * 0.92, color=SERIES[index % len(SERIES)], label=f"{taxon} ({obs['total_excluding_tsa']:,} non-TSA of {obs['total']:,})")
        for x, count in zip(xs, counts):
            right.text(x, count + 1, str(count), ha="center", va="bottom", fontsize=8, color=MUTED)
        for marker, count in zip(MARKERS, counts):
            rows.append({"run": "inventory", "input": f"{taxon} {marker.upper()}", "total": count, "manifest_digest": ""})
    right.set_xticks(range(len(MARKERS)), [marker.upper() for marker in MARKERS], fontsize=9)
    right.set_ylabel("GenBank nucleotide records", fontsize=9, color=MUTED)
    right.grid(axis="y", color="#e4e3df", lw=0.6)
    right.set_axisbelow(True)
    for side in ("top", "right"):
        right.spines[side].set_visible(False)
    right.set_ylim(0, max([obs["marker_counts"].get(m, 0) for obs in taxa.values() for m in MARKERS] + [1]) * 1.35)
    right.legend(frameon=False, fontsize=8, loc="upper right")
    right.set_title("B. GenBank marker records by taxon", loc="left", fontsize=10, color=INK)
    fig.text(0.01, 0.01, "Counts describe deposited records and indexed papers. They do not measure growth "
             "or establish any genotype effect. Marker counts use title keywords.", fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out, dpi=200)
    with open(out.rsplit(".", 1)[0] + ".csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["run", "input", "total", "manifest_digest"])
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main(*sys.argv[1:])
