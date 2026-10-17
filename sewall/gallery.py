"""Static gallery of recorded runs for GitHub Pages.

The gallery publishes only manifests that pass offline verification and a
scan for strings that should not be public. A run may carry a review forum and
a release gate record, and a panel may span several runs; each record is scanned
and verified against its manifests the same way. Reports are re-rendered from the
records; saved HTML is never copied. Nothing here contacts a model or source.
"""

from hashlib import sha256
from html import escape
import json
from pathlib import Path
import re

from .report import render_report


SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z", re.ASCII)
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_RUNS = 50
DENIED = [
    (re.compile(r"/(?:home|scratch|Users)/[A-Za-z0-9_.-]+"), "a local file path"),
    (re.compile(r"api_key=", re.IGNORECASE), "an API key parameter"),
    (re.compile(r"AIza[0-9A-Za-z_-]{30,}"), "a Google API key"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "a secret key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY"), "a private key"),
    (re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}"), "an OAuth access token"),
]
_METRICS = ("records", "actions", "model_calls", "source_requests", "inspected_sources",
            "verified_links", "integrity_gates")
MODES = {
    "synthetic_fixture": "Synthetic fixture",
    "live_public_metadata": "Live public metadata",
    "synthetic_safety_demo": "Synthetic safety demo",
}


class GalleryError(ValueError):
    """A catalog entry cannot be published."""


def verify_manifest(manifest):
    """Return (valid, reason) from the offline check for the manifest's mode."""
    mode = manifest.get("mode")
    if mode == "synthetic_fixture":
        from .graph import replay_manifest
        result = replay_manifest(manifest)
        return result["valid"], result.get("reason", "")
    if mode == "live_public_metadata":
        from .agent import verify_agent_manifest
        result = verify_agent_manifest(manifest)
        return result["valid"], result.get("reason", "")
    if mode == "synthetic_safety_demo":
        from .safety import verify_safety_manifest
        result = verify_safety_manifest(manifest)
        return result["valid"], "; ".join(result.get("errors", [])) or "Safety snapshot and audit chain verified"
    raise GalleryError(f"Unsupported manifest mode: {mode!r}")


def scan(text):
    """Return the reasons a serialized manifest must not be published."""
    found = [label for pattern, label in DENIED if pattern.search(text)]
    try:
        models = json.loads(text).get("context", {}).get("models", {}) or {}
    except (AttributeError, ValueError):
        models = {}
    if any(isinstance(item, dict) and item.get("project") for item in models.values()):
        found.append("a cloud project ID")
    return found


def _render(manifest):
    if manifest["mode"] == "synthetic_safety_demo":
        from .safety import render_safety_report
        return render_safety_report(manifest)
    return render_report(manifest)


def _entries(catalog_path):
    catalog_path = Path(catalog_path)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if (not isinstance(catalog, dict) or set(catalog) - {"title", "description", "runs", "panels"}
            or not isinstance(catalog.get("title"), str) or not isinstance(catalog.get("runs"), list)
            or not 1 <= len(catalog["runs"]) <= MAX_RUNS):
        raise GalleryError("Catalog needs a title and 1 to 50 runs")
    root = catalog_path.parent.resolve()
    slugs = set()
    for run in catalog["runs"]:
        if (not isinstance(run, dict) or set(run) - {"slug", "title", "manifest", "note", "forum", "gate"}
                or not {"slug", "title", "manifest"} <= set(run)
                or any(not isinstance(value, str) for value in run.values())):
            raise GalleryError("Each run needs string slug, title and manifest fields; note, forum and gate are optional")
        if not SLUG.fullmatch(run["slug"]) or run["slug"] in slugs:
            raise GalleryError(f"Invalid or duplicate slug: {run['slug']!r}")
        slugs.add(run["slug"])
        yield catalog, run, _inside(root, run["manifest"])


def _inside(root, relative):
    source = (root / relative).resolve()
    if root not in source.parents or source.suffix != ".json":
        raise GalleryError(f"Record path must be a JSON file inside the catalog directory: {relative}")
    return source


def _panels(catalog_path, slugs):
    root = Path(catalog_path).parent.resolve()
    panels = json.loads(Path(catalog_path).read_text(encoding="utf-8")).get("panels", [])
    if not isinstance(panels, list):
        raise GalleryError("panels must be a list")
    seen = set()
    for panel in panels:
        if (not isinstance(panel, dict) or set(panel) - {"slug", "title", "record", "runs", "note"}
                or not {"slug", "title", "record", "runs"} <= set(panel)
                or not isinstance(panel["runs"], dict) or not panel["runs"]
                or any(not isinstance(panel[k], str) for k in panel if k != "runs")):
            raise GalleryError("Each panel needs slug, title, record and a runs map from researcher to run slug")
        if not SLUG.fullmatch(panel["slug"]) or panel["slug"] in seen:
            raise GalleryError(f"Invalid or duplicate panel slug: {panel['slug']!r}")
        seen.add(panel["slug"])
        missing = [slug for slug in panel["runs"].values() if slug not in slugs]
        if missing:
            raise GalleryError(f"{panel['slug']}: panel names runs not in the catalog: {', '.join(map(str, missing))}")
        yield panel, _inside(root, panel["record"])


def _load_record(label, source, limit):
    if source.stat().st_size > limit:
        raise GalleryError(f"{label}: record exceeds the size limit")
    raw = source.read_bytes()
    text = raw.decode("utf-8")
    problems = scan(text)
    if problems:
        raise GalleryError(f"{label}: record contains " + ", ".join(problems))
    record = json.loads(text)
    if not isinstance(record, dict):
        raise GalleryError(f"{label}: record is not a JSON object")
    return record, raw


def _reviews(run, manifest, root):
    """Verify the run's optional forum and gate records against its manifest."""
    from .forum import render_forum_html, verify_forum
    from .riskgate import render_risk_html, verify_risk_gate
    found, forum = {}, None
    if "forum" in run:
        forum, raw = _load_record(f"{run['slug']} forum", _inside(root, run["forum"]), MAX_RECORD_BYTES)
        result = verify_forum(forum, manifest)
        if not result["valid"]:
            raise GalleryError(f"{run['slug']}: forum verification failed: {result['reason']}")
        found["forum"] = (raw, render_forum_html(forum), {"verdict": forum["synthesis"].get("verdict"),
                                                         "rule_only": forum.get("rule_only")})
    if "gate" in run:
        gate, raw = _load_record(f"{run['slug']} gate", _inside(root, run["gate"]), MAX_RECORD_BYTES)
        result = verify_risk_gate(gate, manifest, forum)
        if not result["valid"]:
            raise GalleryError(f"{run['slug']}: gate verification failed: {result['reason']}")
        found["gate"] = (raw, render_risk_html(gate), {"decision": gate["decision"]})
    return found


def _summary(run, manifest, raw, reason):
    metrics = manifest.get("metrics") or {}
    status = manifest.get("status", "unavailable")
    return {"slug": run["slug"], "title": run["title"], "note": run.get("note", ""),
            "question": manifest.get("question", ""), "mode": manifest["mode"], "status": status,
            "stop_reason": manifest.get("stop_reason"), "manifest_sha256": sha256(raw).hexdigest(),
            "content_digest": manifest.get("content_digest"), "verified": True, "verification": reason,
            "metrics": {key: metrics[key] for key in _METRICS if key in metrics}}


def build_gallery(catalog_path, out_dir) -> dict:
    """Verify, scan and render every catalog run into a static site directory."""
    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        raise GalleryError("Output directory must be new or empty")
    prepared, manifests = [], {}
    root = Path(catalog_path).parent.resolve()
    for catalog, run, source in _entries(catalog_path):
        manifest, raw = _load_record(run["slug"], source, MAX_MANIFEST_BYTES)
        valid, reason = verify_manifest(manifest)
        if not valid:
            raise GalleryError(f"{run['slug']}: verification failed: {reason}")
        reviews = _reviews(run, manifest, root)
        summary = _summary(run, manifest, raw, reason)
        summary["reviews"] = {kind: value[2] for kind, value in reviews.items()}
        prepared.append((run, manifest, raw, summary, reviews))
        manifests[run["slug"]] = manifest
    panels = []
    for panel, source in _panels(catalog_path, manifests):
        from .panel import render_panel_html, verify_panel
        record, raw = _load_record(f"{panel['slug']} panel", source, MAX_RECORD_BYTES)
        result = verify_panel(record, {name: manifests[slug] for name, slug in panel["runs"].items()})
        if not result["valid"]:
            raise GalleryError(f"{panel['slug']}: panel verification failed: {result['reason']}")
        panels.append((panel, raw, render_panel_html(record),
                       {"slug": panel["slug"], "title": panel["title"], "note": panel.get("note", ""),
                        "runs": panel["runs"], "verdicts": result["verdicts"], "rule_only": record.get("rule_only"),
                        "record_sha256": sha256(raw).hexdigest()}))
    out.mkdir(parents=True, exist_ok=True)
    for run, manifest, raw, _, reviews in prepared:
        folder = out / run["slug"]
        folder.mkdir()
        (folder / "manifest.json").write_bytes(raw)
        (folder / "report.html").write_text(_render(manifest), encoding="utf-8")
        for kind, (record_raw, page, _) in reviews.items():
            (folder / f"{kind}.json").write_bytes(record_raw)
            (folder / f"{kind}.html").write_text(page, encoding="utf-8")
    for panel, raw, page, _ in panels:
        folder = out / "panels" / panel["slug"]
        folder.mkdir(parents=True)
        (folder / "panel.json").write_bytes(raw)
        (folder / "panel.html").write_text(page, encoding="utf-8")
    summaries = [item[3] for item in prepared]
    index = {"title": catalog["title"], "description": catalog.get("description", ""), "runs": summaries,
             "panels": [item[3] for item in panels]}
    (out / "gallery.json").write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "index.html").write_text(render_index(index), encoding="utf-8")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return {"out": str(out), "runs": len(summaries), "slugs": [item["slug"] for item in summaries],
            "panels": [item[3]["slug"] for item in panels]}


def _status_label(status):
    if status == "safe_stopped":
        return "Stopped, not successful"
    return status.replace("_", " ").capitalize()


def _review_links(run, esc):
    links = []
    reviews = run.get("reviews", {})
    if "forum" in reviews:
        who = "rule-based reviewers" if reviews["forum"]["rule_only"] else "model reviewers"
        verdict = str(reviews["forum"]["verdict"]).replace("_", " ")
        links.append(f'<a href="{esc(run["slug"])}/forum.html">Review forum</a> ({esc(who)}: {esc(verdict)})')
    if "gate" in reviews:
        links.append(f'<a href="{esc(run["slug"])}/gate.html">Release gate</a> '
                     f'({esc(reviews["gate"]["decision"].replace("_", " "))})')
    return "".join(" · " + link for link in links)


def render_index(index):
    """Return inert HTML with no scripts; every value is escaped."""
    esc = lambda value: escape(str(value), quote=True)
    cards = []
    for run in index["runs"]:
        good = run["status"] == "completed"
        metrics = "".join(f"<li><b>{esc(value)}</b> {esc(key.replace('_', ' '))}</li>" for key, value in run["metrics"].items())
        cards.append(
            f'<article class="card"><div class="row"><span class="badge mode">{esc(MODES[run["mode"]])}</span>'
            f'<span class="badge {"ok" if good else "warn"}">{esc(_status_label(run["status"]))}</span></div>'
            f'<h2><a href="{esc(run["slug"])}/report.html">{esc(run["title"])}</a></h2>'
            f'<p class="question">{esc(run["question"])}</p>'
            + (f'<p>{esc(run["note"])}</p>' if run["note"] else "")
            + (f'<ul class="metrics">{metrics}</ul>' if metrics else "")
            + f'<p class="links"><a href="{esc(run["slug"])}/report.html">Report</a> · '
            f'<a href="{esc(run["slug"])}/manifest.json">Manifest JSON</a>'
            + _review_links(run, esc) + '</p>'
            f'<p class="digest">Verified offline before publication. SHA-256 {esc(run["manifest_sha256"])}</p></article>')
    panels = "".join(
        f'<article class="card"><h2><a href="panels/{esc(p["slug"])}/panel.html">{esc(p["title"])}</a></h2>'
        + (f'<p>{esc(p["note"])}</p>' if p["note"] else "")
        + "<ul class=\"metrics\">" + "".join(f"<li><b>{esc(name)}</b> {esc(verdict.replace('_', ' '))}</li>"
                                     for name, verdict in p["verdicts"].items()) + "</ul>"
        + f'<p class="links"><a href="panels/{esc(p["slug"])}/panel.html">Panel</a> · '
        f'<a href="panels/{esc(p["slug"])}/panel.json">Panel JSON</a></p>'
        + f'<p class="digest">{"Rule-based panelists. " if p["rule_only"] else ""}Verified offline against '
        f'{len(p["runs"])} run manifests. SHA-256 {esc(p["record_sha256"])}</p></article>'
        for p in index.get("panels", []))
    panels = f'<h2 class="section">Cross-run review panels</h2><div class="grid">{panels}</div>' if panels else ""
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{esc(index["title"])}</title>
<style>
:root{{--ink:#17252a;--muted:#55666b;--line:#d5dfe1;--bg:#f6f8f8;--card:#fff;--ok:#0f5549;--okbg:#e0f1e9;--warn:#8a2a1c;--warnbg:#fff0ec;--accent:#0b6e7a}}
@media (prefers-color-scheme: dark){{:root{{--ink:#e6eeef;--muted:#a6b5b8;--line:#33464b;--bg:#10181a;--card:#172226;--ok:#9fe0cb;--okbg:#173a31;--warn:#ffb4a5;--warnbg:#3d1f1a;--accent:#7fd3dd}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1080px;margin:0 auto;padding:32px 16px}}h1{{margin:0 0 8px;font-size:28px}}.lead{{color:var(--muted);max-width:760px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px;margin-top:24px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:16px;min-width:0}}
.card h2{{font-size:18px;margin:10px 0 6px}}a{{color:var(--accent)}}.question{{color:var(--muted);font-size:14px;overflow-wrap:anywhere}}
.row{{display:flex;gap:8px;flex-wrap:wrap}}.badge{{font-size:12px;font-weight:700;border-radius:5px;padding:3px 8px;border:1px solid var(--line)}}
.ok{{color:var(--ok);background:var(--okbg)}}.warn{{color:var(--warn);background:var(--warnbg)}}
.metrics{{list-style:none;padding:0;margin:8px 0;display:flex;flex-wrap:wrap;gap:4px 14px;font-size:13px}}
.section{{margin:32px 0 0;font-size:20px}}.digest{{font-size:11px;color:var(--muted);overflow-wrap:anywhere}}footer{{margin-top:32px;font-size:13px;color:var(--muted)}}
</style>
</head>
<body>
<main>
<h1>{esc(index["title"])}</h1>
<p class="lead">{esc(index["description"])}</p>
<div class="grid">
{"".join(cards)}
</div>
{panels}
<footer>Recorded runs only; this site runs no workflow, model or source query. Reports display untrusted
source text as data. No run establishes a biological finding.</footer>
</main>
</body>
</html>
'''
