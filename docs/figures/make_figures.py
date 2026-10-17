"""Draw the Sewall.ai prototype figures as SVG and PNG with matplotlib.

Run from any directory: python3 docs/figures/make_figures.py
Outputs are written next to this script.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parent
plt.rcParams.update({"font.family": "DejaVu Sans", "svg.fonttype": "path", "svg.hashsalt": "sewall"})

# Okabe-Ito hues (colorblind safe) for edges, with light tints for fills.
BLUE = ("#0072B2", "#DCEBF5")
ORANGE = ("#B07800", "#FCEFD6")
GREEN = ("#009E73", "#D9F0E8")
PURPLE = ("#A0507F", "#F6E3EE")
RED = ("#D55E00", "#FBE1D3")
GRAY = ("#555555", "#F2F2F2")
INK = "#1A1A1A"
MUTED = "#555555"


def canvas(width, height):
    fig = plt.figure(figsize=(width, height))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def box(ax, x, y, w, h, title, body=None, color=GRAY, dashed=False, size=10.5, body_size=8.8, align="center", bold=True):
    edge, fill = color
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.08", linewidth=1.4,
                                edgecolor=edge, facecolor=fill, linestyle=(0, (4, 3)) if dashed else "-"))
    tx = x + w / 2 if align == "center" else x + 0.14
    if body:
        ax.text(tx, y + h - 0.13, title, ha=align, va="top", fontsize=size, color=INK,
                fontweight="bold" if bold else "normal")
        ax.text(tx, y + h - 0.13 - size / 72 * 1.55, body, ha=align, va="top", fontsize=body_size, color=MUTED,
                linespacing=1.3)
    else:
        ax.text(tx, y + h / 2, title, ha=align, va="center", fontsize=size, color=INK,
                fontweight="bold" if bold else "normal", linespacing=1.25)


def arrow(ax, start, end, text=None, color=MUTED, curve=0.0, dashed=False, text_offset=(0, 0.12), size=8.5, ha="center"):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=13, linewidth=1.3, color=color,
                                 connectionstyle=f"arc3,rad={curve}", linestyle=(0, (4, 3)) if dashed else "-",
                                 shrinkA=2, shrinkB=2))
    if text:
        mx, my = (start[0] + end[0]) / 2 + text_offset[0], (start[1] + end[1]) / 2 + text_offset[1]
        ax.text(mx, my, text, ha=ha, va="bottom", fontsize=size, color=color, linespacing=1.2)


def label(ax, x, y, text, size=9, color=MUTED, ha="left", va="center", bold=False, style="normal"):
    ax.text(x, y, text, ha=ha, va=va, fontsize=size, color=color, fontweight="bold" if bold else "normal",
            fontstyle=style, linespacing=1.3)


def title(ax, width, height, text, subtitle):
    label(ax, 0.3, height - 0.35, text, size=15, color=INK, bold=True)
    label(ax, 0.3, height - 0.72, subtitle, size=9.5)


def save(fig, name):
    for suffix in ("svg", "png"):
        fig.savefig(OUT / f"{name}.{suffix}", dpi=200 if suffix == "png" else None, facecolor="white")
    plt.close(fig)


def architecture():
    W, H = 15, 9.4
    fig, ax = canvas(W, H)
    title(ax, W, H, "Sewall.ai architecture (prototype, October 2026)",
          "Every proposed action passes one deterministic controller; models never call sources directly.")

    label(ax, 0.4, 8.2, "WHO PLANS", size=9, color=BLUE[0], bold=True)
    box(ax, 0.4, 6.75, 3.3, 1.25, "MCP host model",
        "Claude, Gemini CLI, ChatGPT or an\nopen-weight model writes a plan\n(run_planned_research)", BLUE)
    box(ax, 0.4, 5.25, 3.3, 1.25, "Planner model",
        "Vertex AI, Anthropic or an\nOpenAI-compatible server,\none bounded call per step", BLUE)
    box(ax, 0.4, 3.75, 3.3, 1.25, "Scripted plan",
        "Fixed or lab-manager steps,\nchecked like model proposals", BLUE)

    cx, cy, cw, ch = 4.6, 3.75, 5.0, 4.25
    edge, fill = ORANGE
    ax.add_patch(FancyBboxPatch((cx, cy), cw, ch, boxstyle="round,pad=0,rounding_size=0.12", linewidth=1.8,
                                edgecolor=edge, facecolor=fill))
    label(ax, cx + cw / 2, cy + ch - 0.28, "Deterministic controller", size=12, color=INK, ha="center", bold=True)
    label(ax, cx + cw / 2, cy + ch - 0.6, "run_agent: checks, budgets, policies, gates, audit", size=8.8, ha="center")
    chips = [("Skill registry", "versioned descriptors\nand their digests"),
             ("Action checks", "no guessed IDs,\nrepeats or unknown actions"),
             ("Access policies", "metadata_only, summary_only\nor unconfigured"),
             ("Budgets", "actions, model calls,\nrecords and time"),
             ("Integrity gates", "before and after each\naction, before review"),
             ("Event log", "hash-chained events\nfor offline replay")]
    for i, (name, body) in enumerate(chips):
        col, row = i % 2, i // 2
        box(ax, cx + 0.2 + col * 2.35, cy + 2.45 - row * 1.12, 2.25, 0.98, name, body, ("#C79A3A", "#FFFFFF"),
            size=9.5, body_size=8)

    label(ax, 10.95, 8.2, "SKILLS AND PUBLIC SOURCES", size=9, color=GREEN[0], bold=True)
    skills = [("search", "PubMed, GEO or BioProject summaries", False),
              ("citations", "papers listed in a returned GEO record", False),
              ("taxon_inventory", "GenBank record counts; no sequences", False),
              ("chlorophyll_timeseries", "Sentinel-2 NDCI inside Earth Engine", False),
              ("alphaearth_context", "AlphaEarth annual summary in Earth Engine", False),
              ("flood_sensors", "latest NOAA and USGS water levels by area", False),
              ("assess_source", "local access-question draft; nothing sent", False)]
    for i, (name, body, simulated) in enumerate(skills):
        y = 7.41 - i * 0.62
        box(ax, 10.95, y, 3.7, 0.54, name, body, GREEN if not simulated else ("#009E73", "#FFFFFF"),
            dashed=simulated, size=9.5, body_size=8, align="left")

    for y in (7.37, 5.87, 4.37):
        arrow(ax, (3.72, y), (cx - 0.02, y), color=BLUE[0])
    label(ax, 4.15, 8.3, "proposed action\n(JSON)", size=8, color=BLUE[0], ha="center")
    arrow(ax, (cx + cw + 0.02, 6.5), (10.92, 6.5), "checked\naction", color=ORANGE[0], size=8)
    arrow(ax, (10.92, 5.4), (cx + cw + 0.02, 5.4), "bounded\nsummaries", color=GREEN[0], text_offset=(0, -0.55), size=8)

    box(ax, 4.6, 2.0, 2.4, 1.0, "Separate reviewer",
        "model or script; sees\nrecords and checked links", PURPLE)
    box(ax, 7.35, 2.0, 2.25, 1.0, "manifest.json",
        "events, records, model\ncalls, actions, policies", PURPLE)
    box(ax, 9.95, 2.0, 2.15, 1.0, "report.html", "self-contained;\nno network requests", PURPLE)
    box(ax, 12.45, 2.0, 2.2, 1.0, "Offline replay", "agent-replay; no model\nor source rerun", PURPLE)
    arrow(ax, (5.8, cy - 0.02), (5.8, 3.02), color=PURPLE[0])
    arrow(ax, (8.47, cy - 0.02), (8.47, 3.02), color=PURPLE[0])
    arrow(ax, (7.02, 2.5), (7.33, 2.5), color=PURPLE[0])
    arrow(ax, (9.62, 2.5), (9.93, 2.5), color=PURPLE[0])
    arrow(ax, (12.12, 2.5), (12.43, 2.5), color=PURPLE[0])

    label(ax, 0.4, 1.25, "The Earth Engine Skills ran live on October 14, 2026 (York and Lafayette Rivers, 2022; both runs pass "
          "offline replay). They load only when an Earth Engine project is configured.", size=8.5)
    label(ax, 0.4, 0.88, "The flood_sensors Skill read live NOAA and USGS sensors over six Chesapeake areas on October 15, 2026 "
          "(fig7); the run passes offline replay.", size=8.5)
    label(ax, 0.4, 0.51, "Not yet implemented: federation with data custodians. This release reads public NCBI metadata, "
          "Earth Engine summaries and sensor readings and makes no biological claim.", size=8.5)
    save(fig, "fig1-architecture")


def lifecycle():
    W, H = 17, 9.2
    fig, ax = canvas(W, H)
    title(ax, W, H, "One Sewall.ai run, step by step",
          "The controller loops until the planner finishes, a check fails, a budget runs out or a gate stops the run.")
    y, h, w = 5.6, 1.15, 1.9
    steps = [("Planner\nproposes", "one action\nas JSON", BLUE), ("Controller\nchecks", "Skill, arguments,\nrepeats, budget", ORANGE),
             ("Gate before\naction", "integrity\ncritiques?", ORANGE), ("Execute\nSkill", "bounded public\nrequest", GREEN),
             ("Record\nevents", "hash chain,\ngraph nodes", ORANGE), ("Gate after\naction", "integrity\ncritiques?", ORANGE)]
    xs = [0.5 + i * 2.35 for i in range(len(steps))]
    for x, (name, body, color) in zip(xs, steps):
        box(ax, x, y, w, h, name, None, color, size=10)
        label(ax, x + w / 2, y - 0.3, body, size=8.3, ha="center", va="top")
    box(ax, 0.5, 7.05, w, 0.6, "Question + budgets", None, GRAY, size=9.5)
    arrow(ax, (0.5 + w / 2, 7.03), (0.5 + w / 2, y + h + 0.02))
    for a, b in zip(xs, xs[1:]):
        arrow(ax, (a + w + 0.02, y + h / 2), (b - 0.02, y + h / 2))
    ax.add_patch(FancyArrowPatch((xs[-1] + w / 2, y + h + 0.02), (xs[0] + w * 0.75, y + h + 0.02), arrowstyle="-|>",
                                 mutation_scale=13, linewidth=1.3, color=MUTED, connectionstyle="arc3,rad=0.12"))
    label(ax, 7.1, 7.72, "next step", size=8.5, ha="center", va="bottom")

    def exit_pill(x, yy, text, pw=w):
        ax.add_patch(FancyBboxPatch((x, yy), pw, 0.5, boxstyle="round,pad=0,rounding_size=0.2", linewidth=1.3,
                                    edgecolor=RED[0], facecolor=RED[1]))
        label(ax, x + pw / 2, yy + 0.25, text, size=8.3, color=INK, ha="center")

    # Exits from the loop, each with its reason.
    py = 3.75
    for x, text, reason in ((xs[2], "safe_stopped", "critical critique"), (xs[5], "safe_stopped", "critical critique")):
        arrow(ax, (x + w / 2, y - 0.82), (x + w / 2, py + 0.52), reason, color=RED[0], text_offset=(0.1, -0.12), ha="left", size=8)
        exit_pill(x, py, text)
    by = 2.0
    for x, text, reason in ((xs[0], "failed", "planner error"), (xs[1], "failed or\nbudget_exhausted", "invalid action;\nno budget left")):
        arrow(ax, (x + 0.35, y - 0.82), (x + 0.35, by + 0.72), reason, color=RED[0], text_offset=(0.1, -0.3), ha="left", size=8)
        ax.add_patch(FancyBboxPatch((x, by), w, 0.7, boxstyle="round,pad=0,rounding_size=0.2", linewidth=1.3,
                                    edgecolor=RED[0], facecolor=RED[1]))
        label(ax, x + w / 2, by + 0.35, text, size=8.3, color=INK, ha="center")

    # The finish path.
    finish = [("finish\nproposed", BLUE), ("Gate before\nreview", ORANGE), ("Separate\nreview", PURPLE), ("Manifest\nand report", PURPLE)]
    for x, (name, color) in zip(xs[2:], finish):
        box(ax, x, by, w, h, name, None, color, size=10)
    arrow(ax, (xs[1] + w - 0.3, y - 0.02), (xs[2] - 0.02, by + h - 0.15), "finish", text_offset=(0.05, 0.25), ha="left")
    for a, b in zip(xs[2:], xs[3:]):
        arrow(ax, (a + w + 0.02, by + h / 2), (b - 0.02, by + h / 2))
    label(ax, xs[3] + w / 2, by - 0.12, "critique: safe_stopped", size=8, color=RED[0], ha="center", va="top")
    label(ax, xs[4] + w / 2, by - 0.12, "invalid: needs_review", size=8, color=PURPLE[0], ha="center", va="top")

    sx, sy = 15.0, 1.6
    ax.add_patch(FancyBboxPatch((sx - 0.2, sy - 0.2), 2.0, 5.25, boxstyle="round,pad=0,rounding_size=0.08",
                                linewidth=1.0, edgecolor="#BBBBBB", facecolor="#FFFFFF"))
    label(ax, sx, sy + 4.75, "Final status", size=10, color=INK, bold=True)
    statuses = [("completed", "finished; review\nchecked", GREEN), ("budget_exhausted", "a budget ran out", ORANGE),
                ("failed", "invalid action or\nplanner failure", RED), ("no_evidence", "no public record\nretrieved", GRAY),
                ("needs_review", "review failed\nvalidation", PURPLE), ("safe_stopped", "integrity stop", RED)]
    for i, (name, body, color) in enumerate(statuses):
        yy = sy + 4.25 - i * 0.75
        label(ax, sx, yy, name, size=8.6, color=color[0], bold=True)
        label(ax, sx, yy - 0.12, body, size=7.6, va="top")

    label(ax, 0.5, 1.0, "Only completed exits with code 0. A safe stop is never relabeled, and a run that retrieved no "
          "public record ends as no_evidence even if the planner finished.", size=8.5)
    label(ax, 0.5, 0.6, "Model calls are counted before transport, so failed calls still use the budget; one call is "
          "reserved for the separate review.", size=8.5)
    save(fig, "fig2-run-lifecycle")


def safe_stop():
    W, H = 15, 7.6
    fig, ax = canvas(W, H)
    title(ax, W, H, "Sewall.ai safe stop: the bundled live demonstration",
          "configs/scripted-safe-stop-demo.json injects one labeled fault after action 2; the stop path is the same for real critiques.")
    w, h = 2.05, 0.85
    nodes = {"question": (0.4, 4.6, "question", GRAY), "plan1": (2.75, 4.6, "plan:1", BLUE),
             "action1": (5.1, 4.6, "action:1\nsearch PubMed", GREEN), "pubmed": (7.45, 5.65, "PubMed records", GREEN),
             "plan2": (5.1, 3.15, "plan:2", BLUE), "action2": (7.45, 3.15, "action:2\nGenBank inventory", RED),
             "inventory": (9.8, 3.15, "inventory node\nM. polykrikoides", RED)}
    for key, (x, y, text, color) in nodes.items():
        box(ax, x, y, w, h, text, None, color, size=9.2, bold=key in ("action2", "inventory"))
    box(ax, 9.8, 1.7, w, h, "action:3\nnot dispatched", None, GRAY, dashed=True, size=9.2, bold=False)
    box(ax, 12.15, 1.7, w, h, "review\nwithheld", None, GRAY, dashed=True, size=9.2, bold=False)

    def mid(key, side):
        x, y, *_ = nodes[key]
        return {"r": (x + w + 0.02, y + h / 2), "l": (x - 0.02, y + h / 2), "b": (x + w / 2, y - 0.02),
                "t": (x + w / 2, y + h + 0.02)}[side]

    arrow(ax, mid("question", "r"), mid("plan1", "l"))
    arrow(ax, mid("plan1", "r"), mid("action1", "l"))
    arrow(ax, mid("action1", "r"), mid("pubmed", "l"), "produces", text_offset=(-0.35, 0.05))
    arrow(ax, mid("action1", "b"), mid("plan2", "t"), "depends_on", text_offset=(0.62, -0.08))
    arrow(ax, mid("plan2", "r"), mid("action2", "l"))
    arrow(ax, mid("action2", "r"), mid("inventory", "l"), color=RED[0])

    cx, cy = 7.45, 1.75
    box(ax, cx - 0.15, cy - 0.2, 2.2, 1.0, "critical critique", "missing_provenance\non action:2", RED, size=9.2, body_size=8)
    arrow(ax, (cx + 0.95, cy + 0.82), mid("action2", "b"), color=RED[0])
    label(ax, 0.4, 2.85, "Legend", size=9, color=INK, bold=True)
    for i, (text, color, dashed) in enumerate([("completed, kept", GREEN, False), ("quarantined", RED, False),
                                                 ("never run or withheld", GRAY, True)]):
        box(ax, 0.4, 2.2 - i * 0.55, 0.45, 0.35, "", None, color, dashed=dashed)
        label(ax, 1.0, 2.37 - i * 0.55, text, size=8.6)

    px = 12.45
    label(ax, px, 6.25, "What the controller does", size=10, color=INK, bold=True)
    steps = ["1. The gate after action 2\n    receives a critical critique.",
             "2. Nodes downstream of the critique\n    (depends_on, produces): completed\n    ones quarantined, others blocked.",
             "3. No further action, model call\n    or review is dispatched.",
             "4. Status safe_stopped; the report\n    shows STOPPED, NOT SUCCESSFUL;\n    the CLI exits with code 1."]
    yy = 5.85
    for text in steps:
        label(ax, px, yy, text, size=8.4, va="top")
        yy -= 0.25 * (text.count("\n") + 1) + 0.25
    ax.add_patch(FancyBboxPatch((12.15, 0.45), 2.4, 0.75, boxstyle="round,pad=0,rounding_size=0.06", linewidth=2,
                                edgecolor=RED[0], facecolor="#FFFFFF"))
    label(ax, 13.35, 0.83, "STOPPED, NOT SUCCESSFUL", size=8.8, color=RED[0], ha="center", bold=True)
    label(ax, 0.4, 0.75, "Records retrieved before the critique stay in the manifest for audit. Built-in gates also "
          "recompute source digests,\nso evidence changed after retrieval raises tampered_evidence. Replay checks the "
          "quarantine against the recorded graph.", size=8.4)
    save(fig, "fig3-safe-stop")


def replay():
    W, H = 15, 7.8
    fig, ax = canvas(W, H)
    title(ax, W, H, "Sewall.ai offline replay (sewall agent-replay)",
          "Replay re-derives the saved results from the recorded events. It repeats no model inference and no source query.")
    box(ax, 0.4, 3.85, 2.4, 2.2, "manifest.json",
        "inputs and plan\nhash-chained events\ngraph, records\nmodel calls, actions\nmetadata summary", PURPLE)
    checks = [("Content digest", "matches the saved manifest"),
              ("Event hash chain", "sequence and previous hashes intact"),
              ("Header binding", "question, budgets, plan, Skills\nequal the first recorded event"),
              ("Graph reduction", "events rebuild the saved graph exactly"),
              ("Projections", "events rebuild records, calls, actions,\npolicies, request drafts, metrics"),
              ("Allowed actions", "every action in the recorded Skill set"),
              ("Safe stop", "quarantine equals the descendants;\nreview withheld"),
              ("Evidence", "records rechecked; links recomputed\nfrom explicit source fields"),
              ("Summary and status", "review rechecked; final event\nmatches the saved status")]
    x0, y0, cw, chh = 3.55, 5.55, 2.85, 0.8
    for i, (name, body) in enumerate(checks):
        col, row = i // 3, i % 3
        x, y = x0 + col * 3.05, y0 - row * 1.0
        box(ax, x, y, cw, chh, f"{i + 1}. {name}", body, ORANGE, size=9.3, body_size=7.9, align="left")
    arrow(ax, (2.82, 4.95), (3.5, 4.95), color=PURPLE[0])
    ax.add_patch(FancyArrowPatch((x0 + 3 * 3.05 - 0.15, 4.95), (13.0, 4.95), arrowstyle="-|>", mutation_scale=13,
                                 linewidth=1.3, color=MUTED))
    box(ax, 13.05, 5.2, 1.6, 0.8, "valid", "all checks pass", GREEN, size=10, body_size=8)
    box(ax, 13.05, 3.9, 1.6, 0.8, "invalid", "first failed check", RED, size=10, body_size=8)
    ax.add_patch(FancyBboxPatch((3.55, 0.95), 8.95, 0.95, boxstyle="round,pad=0,rounding_size=0.06", linewidth=1.0,
                                edgecolor="#BBBBBB", facecolor="#FFFFFF"))
    label(ax, 3.75, 1.6, "Not repeated during replay", size=9.3, color=INK, bold=True)
    label(ax, 3.75, 1.22, "model calls re-executed: 0     source requests re-executed: 0", size=8.8)
    label(ax, 0.4, 0.5, "Replay confirms the saved manifest is internally consistent with its hash-chained events. "
          "Custodian authenticity calls for signed attestations.", size=8.5)
    save(fig, "fig4-offline-replay")


def bloom():
    W, H = 15, 7.0
    fig, ax = canvas(W, H)
    title(ax, W, H, "Chesapeake bloom MVP: what runs today",
          "Question: what public evidence exists on Margalefidinium polykrikoides blooms in the Lafayette River?")
    legend = [("Live NCBI or Earth Engine, in recorded scripted runs", GREEN, False),
              ("Planned; not implemented", GRAY, True)]
    for i, (text, color, dashed) in enumerate(legend):
        x = 0.4 + i * 4.6
        box(ax, x, 5.55, 0.45, 0.32, "", None, color, dashed=dashed)
        label(ax, x + 0.6, 5.71, text, size=8.8)
    w, h = 2.25, 1.15
    row1 = [("1. PubMed search", "search\nbloom reports in\nChesapeake waters", GREEN, False),
            ("2. GenBank inventory", "taxon_inventory\nM. polykrikoides\nrecord counts by marker", GREEN, False),
            ("3. GenBank inventory", "taxon_inventory\nAlexandrium monilatum\nTSA contigs counted apart", GREEN, False),
            ("4. BioProject search", "search\npublic sequencing\nprojects", GREEN, False)]
    row2 = [("5. Sentinel-2 NDCI", "chlorophyll_timeseries\nper-scene water-pixel\nmeans, named site", GREEN, False),
            ("6. AlphaEarth context", "alphaearth_context\nannual 64-band mean\nover the same site", GREEN, False),
            ("7. Align windows", "proxy inside versus\noutside reported\nbloom windows", GRAY, True),
            ("8. Review and report", "separate review,\nreplayable manifest,\nHTML report", PURPLE, False)]
    for r, row in enumerate((row1, row2)):
        y = 3.95 - r * 2.1
        for i, (name, body, color, dashed) in enumerate(row):
            x = 0.4 + i * 2.75
            box(ax, x, y, w, h, name, body, color, dashed=dashed, size=9.6, body_size=8.2)
            if i:
                arrow(ax, (x - 0.48, y + h / 2), (x - 0.02, y + h / 2), dashed=dashed or row[i - 1][3])
    arrow(ax, (0.4 + 3 * 2.75 + w / 2, 3.93), (0.4 + 3 * 2.75 + w / 2, 1.85 + h + 0.02), "records", text_offset=(0.4, -0.1))
    label(ax, 11.6, 4.7, "Limits stated in every report", size=9.5, color=INK, bold=True)
    notes = ["NDCI is a chlorophyll proxy. It does\nnot identify a species or confirm a bloom.",
             "The site is an approximate bounding\nbox, not a surveyed polygon.",
             "AlphaEarth dimensions are learned\nfeatures, not physical measurements.",
             "Record retrieval validates no\nbiological claim."]
    yy = 4.35
    for text in notes:
        label(ax, 11.6, yy, text, size=8.3, va="top")
        yy -= 0.82
    label(ax, 0.4, 0.75, "Steps 1 to 4 are configs/scripted-bloom-demo.json, run on live NCBI without model keys.", size=8.5)
    label(ax, 0.4, 0.4, "Steps 5 and 6 ran live on Earth Engine on October 14, 2026 in configs/scripted-lafayette-2022-satellite.json "
          "and the York River script (fig6).", size=8.5)
    save(fig, "fig5-bloom-mvp")


if __name__ == "__main__":
    architecture()
    lifecycle()
    safe_stop()
    replay()
    bloom()
    print("wrote", sorted(path.name for path in OUT.glob("fig*")))
