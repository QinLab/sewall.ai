"""Cross-run critique panel: agentic researchers critique each other's runs.

Each researcher submits one verified run manifest. In round 1 every
researcher critiques the other researchers' runs, never their own: concerns,
support, questions and limitations, including comparisons that cite nodes in
the critic's own run. In round 2 each run's owner rebuts the critiques of
their run by answering or conceding, and peers who would have raised the same
point agree with it. A deterministic moderator then applies the forum's rules
to each run separately and records which concerns the owner conceded.

Posts carry a target (the researcher whose run is under critique) and must
cite nodes in that run, nodes in another panel run written as
"researcher::node", or earlier posts. They pass the same checks as forum
posts and are hash-chained from the digests of every submitted manifest. The
panel never validates a biological claim.
"""

from copy import deepcopy
import html
import re

from .agent import verify_agent_manifest
from .forum import KINDS, MAX_REFS, _BLOOM, _NAME, _jsonable, _obs, check_draft, manifest_view, synthesize
from .graph import digest

SCHEMA_VERSION = "0.1.0"
MIN_RUNS, MAX_RUNS = 2, 6
MAX_POSTS_PER_TURN = 12
ROUNDS = (1, 2)
_CROSS = "::"


def _bloom_records(view):
    return [r["id"] for r in view["records"] if _BLOOM.search(r["title"])]


def _candidates(me, views, target):
    """Points researcher `me` would raise about `target`'s run, most serious first."""
    own, view, out = views[me], views[target], []

    def add(kind, topic, text, refs, **extra):
        out.append({"kind": kind, "topic": topic, "text": text, "refs": list(dict.fromkeys(refs))[:MAX_REFS],
                    "target": target, **extra})

    if not view["records"] and not _obs(view, "taxon_inventory"):
        add("concern", "no-records", "Your run retrieved no literature or sequence records, so nothing ties its "
            "observations to published work.", ["question"], severity="major")
    if _BLOOM.search(view["question"]):
        add("concern", "no-in-situ", "Your run holds no in situ cell count or species observation for the bloom, "
            "so a satellite signal cannot be attributed to the named organism.", ["question"], severity="major")
    for item in view["failed_actions"]:
        add("concern", f"failed-action-{item['index']}", f"Your action {item['index']} ({item['action']}) ended "
            f"{item['status']}, which leaves a gap in the evidence.", [item["node"]],
            severity="major" if item["action"] == "search" else "minor")
    for o in _obs(view, "chlorophyll_timeseries"):
        if o["scenes_with_water_pixels"] < 3:
            add("concern", f"sparse-{o['start'][:7]}", f"Your {o['start'][:7]} NDCI value rests on "
                f"{o['scenes_with_water_pixels']} usable water scenes, too few to compare with other months or runs.",
                [o["node"]], severity="minor")
    mine, theirs = _obs(own, "alphaearth_context"), _obs(view, "alphaearth_context")
    if mine and theirs and {o["scale_meters"] for o in mine} != {o["scale_meters"] for o in theirs}:
        add("concern", "alphaearth-scale", f"Your AlphaEarth context was reduced at "
            f"{', '.join(str(s) for s in sorted({o['scale_meters'] for o in theirs}))} m and mine at "
            f"{', '.join(str(s) for s in sorted({o['scale_meters'] for o in mine}))} m, so our landscape "
            "summaries are not directly comparable.",
            [theirs[0]["node"], f"{me}{_CROSS}{mine[0]['node']}"], severity="minor")
    cited = set(view["review_summary"]["record_ids"])
    unreviewed = [o["node"] for o in view["observations"]
                  if o["operation"] not in ("search", "taxon_inventory") and o["node"] not in cited]
    if unreviewed:
        add("concern", "review-omits-observations", f"Your run's review covers literature records only and "
            f"skips {len(unreviewed)} computed observation{'s' if len(unreviewed) != 1 else ''}.",
            ([n for n in ("review",) if n in view["node_ids"]] + unreviewed), severity="minor")
    shared = sorted({r["id"] for r in view["records"]} & {r["id"] for r in own["records"]})
    if shared:
        add("support", "shared-records", f"My run independently retrieved {len(shared)} of the same records, "
            "so this part of your evidence is reproducible across runs.",
            shared + [f"{me}{_CROSS}{shared[0]}"])
    return out


def rule_panelist(me, views, round_, thread):
    """Critiques peers' runs in round 1; rebuts critiques of its own run and seconds peers in round 2."""
    posts = []
    if round_ == 1:
        for target in views:
            if target == me:
                continue
            for item in _candidates(me, views, target):
                if not any(p["target"] == target and p.get("topic") == item["topic"] for p in thread + posts):
                    posts.append(item)
        return posts
    replied = {p["reply_to"] for p in thread if p["author"] == me and p["kind"] == "reply"}
    own = views[me]
    for post in thread:
        if post["kind"] != "concern" or post["author"] == me or post["id"] in replied:
            continue
        if post["target"] == me:
            bloom = _bloom_records(own)
            if post.get("topic") == "no-in-situ" and bloom:
                posts.append({"kind": "reply", "stance": "answer", "reply_to": post["id"], "target": me,
                              "text": f"{len(bloom)} of my retrieved paper titles mention{'s' if len(bloom) == 1 else ''} the "
                                      "bloom; those papers may hold in situ observations, though my run reads only "
                                      "their metadata.",
                              "refs": [post["id"], *bloom][:MAX_REFS]})
            elif post.get("topic") == "alphaearth-scale":
                nodes = [o["node"] for o in _obs(own, "alphaearth_context")]
                posts.append({"kind": "reply", "stance": "answer", "reply_to": post["id"], "target": me,
                              "text": "The scale is recorded on each observation node, so a comparison can "
                                      "resample both runs to the coarser scale.",
                              "refs": [post["id"], *nodes][:MAX_REFS]})
            elif post.get("topic"):
                posts.append({"kind": "reply", "stance": "agree", "reply_to": post["id"], "target": me,
                              "text": "Conceded; the run should be revised on this point.", "refs": [post["id"]]})
        elif any(c["topic"] == post.get("topic") for c in _candidates(me, views, post["target"])
                 if c["kind"] == "concern"):
            posts.append({"kind": "reply", "stance": "agree", "reply_to": post["id"], "target": post["target"],
                          "text": "I reached the same concern from my own reading of this run.", "refs": [post["id"]]})
    return posts


PANEL_PROMPT = """You are an agentic researcher on a Sewall.ai critique panel. Each panelist submitted one recorded
research run; you critique the other panelists' runs, never your own, and defend your own run when it is
critiqued. You review what each run retrieved and how; you never validate or assert a biological claim.
Return only JSON: {"posts": [...]}, at most 12 posts. Each post has:
  target: the panelist whose run the post is about (in round 1, never yourself)
  kind: concern | support | question | limitation | reply
  text: plain text, at most 1000 characters
  refs: 1 to 12 ids: a node id from the target run's node_ids, "panelist::node_id" for a node in any run,
        or an earlier post id
  severity: minor | major (concerns only)
  reply_to and stance (agree | dispute | answer): replies only; reply_to is an earlier post by another
        panelist, and target must equal that post's target
  topic: optional short slug
In round 1 critique. In round 2 answer or concede critiques of your own run and reply to others."""


class ModelPanelist:
    """A panelist backed by a model client's complete_json."""

    def __init__(self, client, focus="methods, evidence and comparability across runs"):
        self.client, self.focus = client, str(focus)[:300]
        self.last_call = {}

    def __call__(self, me, views, round_, thread):
        payload = {"panelist": me, "focus": self.focus, "round": round_, "runs": views,
                   "thread": [{k: p.get(k) for k in ("id", "author", "target", "kind", "severity", "reply_to",
                                                    "stance", "topic", "text", "refs")} for p in thread]}
        response = self.client.complete_json(PANEL_PROMPT, payload)
        self.last_call = {k: response.get(k) for k in ("provider", "model", "model_version", "request_id",
                                                       "response_sha256", "usage", "latency_seconds")}
        output = response.get("output")
        if not isinstance(output, dict) or not isinstance(output.get("posts"), list):
            raise ValueError("model panelist output must be an object with a posts list")
        return output["posts"]


def _known(views, target):
    return set(views[target]["node_ids"]) | {f"{name}{_CROSS}{node}" for name, view in views.items()
                                             for node in view["node_ids"]}


def check_panel_draft(draft, author, round_, views, thread):
    """Check a panel post: a valid target, then the forum's post rules against that run's nodes."""
    if not isinstance(draft, dict) or not isinstance(draft.get("target"), str) or draft["target"] not in views:
        raise ValueError("target must name a panelist")
    target = draft["target"]
    body = {k: v for k, v in draft.items() if k != "target"}
    if body.get("kind") in KINDS and body["kind"] != "reply" and target == author:
        raise ValueError("a panelist cannot critique or endorse their own run")
    checked = check_draft(body, author, round_, _known(views, target), thread)
    if checked["kind"] == "reply" and next(p for p in thread if p["id"] == checked["reply_to"])["target"] != target:
        raise ValueError("a reply must have the same target as the post it answers")
    return {"target": target, **checked}


def _views(entries):
    names = [name for name, _, _ in entries]
    if not MIN_RUNS <= len(entries) <= MAX_RUNS or len(set(names)) != len(names) or not all(
            isinstance(n, str) and _NAME.fullmatch(n) and _CROSS not in n for n in names):
        raise ValueError(f"panel needs {MIN_RUNS} to {MAX_RUNS} panelists with distinct slug names")
    for name, manifest, _ in entries:
        if not verify_agent_manifest(manifest).get("valid"):
            raise ValueError(f"Manifest for {name} failed verification; the panel reviews only verified runs")
    digests = [m["content_digest"] for _, m, _ in entries]
    if len(set(digests)) != len(digests):
        raise ValueError("each panelist must submit a different run")
    return {name: manifest_view(manifest) for name, manifest, _ in entries}


def _synthesis(views, posts):
    runs = {}
    for name, view in views.items():
        mine = [p for p in posts if p["target"] == name]
        result = synthesize(view, mine)
        result["conceded"] = sorted({p["reply_to"] for p in mine if p["author"] == name and p.get("stance") == "agree"})
        runs[name] = result
    return {"runs": runs, "biological_claims_validated": False}


def run_panel(entries, topic=None) -> dict:
    """Run a cross-critique panel. entries: (researcher, manifest, panelist or None for the rule panelist)."""
    entries = [(name, manifest, panelist or rule_panelist) for name, manifest, panelist in entries]
    views = _views(entries)
    posts, rejected, calls = [], [], []
    for round_ in ROUNDS:
        for name, _, panelist in entries:
            try:
                drafts = panelist(name, deepcopy(views), round_, deepcopy(posts))
                if not isinstance(drafts, list):
                    raise ValueError("panelist must return a list of posts")
                status = "completed"
            except Exception as exc:  # A failing panelist is recorded, not fatal.
                drafts, status = [], f"failed ({type(exc).__name__})"
            if isinstance(panelist, ModelPanelist):
                calls.append({"author": name, "round": round_, "status": status, **panelist.last_call})
                panelist.last_call = {}
            elif status != "completed":
                rejected.append({"author": name, "round": round_, "reason": f"panelist {status}", "draft_sha256": None})
            for position, draft in enumerate(drafts):
                try:
                    if position >= MAX_POSTS_PER_TURN:
                        raise ValueError(f"more than {MAX_POSTS_PER_TURN} posts in one turn")
                    body = check_panel_draft(draft, name, round_, views, posts)
                except ValueError as exc:
                    rejected.append({"author": name, "round": round_, "reason": str(exc)[:300],
                                     "draft_sha256": digest(draft) if _jsonable(draft) else None})
                    continue
                posts.append({"id": f"post:{len(posts) + 1}", "round": round_, "author": name, **body})
    runs = [{"researcher": name, "manifest_digest": manifest["content_digest"], "question": manifest["question"],
             "panelist": "model" if isinstance(panelist, ModelPanelist) else "rule"}
            for name, manifest, panelist in entries]
    binding = digest([run["manifest_digest"] for run in runs])
    prev = binding
    for post in posts:
        post["prev"] = prev
        post["hash"] = digest({k: v for k, v in post.items() if k != "hash"})
        prev = post["hash"]
    panel = {"schema_version": SCHEMA_VERSION, "kind": "sewall_review_panel",
             "topic": str(topic)[:300] if topic else "Cross-run critique", "binding": binding, "runs": runs,
             "rule_only": all(run["panelist"] == "rule" for run in runs), "posts": posts, "rejected": rejected,
             "model_calls": calls, "synthesis": {"author": "moderator", "kind": "synthesis", "prev": prev,
                                                 **_synthesis(views, posts)},
             "limitations": ["The panel reviews retrieved metadata and computed summaries; it validates no "
                             "biological claim.",
                             "Rule panelists apply the same fixed checks, so their agreement is not independent "
                             "expert judgment.",
                             "A conceded concern is the owner's acceptance of a critique, not a revision of the run.",
                             "Model panelist posts are checked for structure and citations, not for truth."]}
    panel["content_digest"] = digest({k: v for k, v in panel.items() if k != "content_digest"})
    return panel


def verify_panel(panel: dict, manifests: dict) -> dict:
    """Check bindings, the post chain, citations and every run's synthesis. manifests maps researcher to manifest."""
    try:
        if not isinstance(panel, dict) or panel.get("schema_version") != SCHEMA_VERSION or \
                panel.get("kind") != "sewall_review_panel":
            raise ValueError("Unsupported panel")
        if digest({k: v for k, v in panel.items() if k != "content_digest"}) != panel.get("content_digest"):
            raise ValueError("Panel content digest mismatch")
        names = [run["researcher"] for run in panel["runs"]]
        if set(manifests) != set(names):
            raise ValueError("Supply exactly one manifest per panelist")
        entries = [(name, manifests[name], None) for name in names]
        views = _views(entries)
        for run in panel["runs"]:
            if manifests[run["researcher"]]["content_digest"] != run["manifest_digest"]:
                raise ValueError(f"Panel is bound to a different run for {run['researcher']}")
        prev = digest([run["manifest_digest"] for run in panel["runs"]])
        if panel["binding"] != prev:
            raise ValueError("Panel binding mismatch")
        earlier = []
        for number, post in enumerate(panel["posts"], 1):
            if post.get("id") != f"post:{number}" or post.get("prev") != prev:
                raise ValueError(f"Broken post chain at post {number}")
            if digest({k: v for k, v in post.items() if k != "hash"}) != post.get("hash"):
                raise ValueError(f"Post {number} hash mismatch")
            if post["author"] not in views:
                raise ValueError(f"Post {number} has an unknown author")
            body = {k: post[k] for k in ("target", "kind", "text", "refs", "severity", "reply_to", "stance", "topic")
                    if k in post}
            check_panel_draft(body, post["author"], post["round"], views, earlier)
            prev = post["hash"]
            earlier.append(post)
        expected = _synthesis(views, panel["posts"])
        if {k: panel["synthesis"].get(k) for k in expected} != expected or panel["synthesis"].get("prev") != prev:
            raise ValueError("Moderator synthesis does not match the posts")
        if panel["rule_only"] and run_panel(entries, panel["topic"])["content_digest"] != panel["content_digest"]:
            raise ValueError("Rule-only panel does not rebuild to the same digest")
        return {"valid": True, "posts": len(panel["posts"]),
                "verdicts": {name: run["verdict"] for name, run in panel["synthesis"]["runs"].items()},
                "content_digest": panel["content_digest"]}
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        return {"valid": False, "reason": str(exc)[:300]}


def render_panel_html(panel: dict) -> str:
    """Self-contained HTML page: one section per run, critiques threaded by reply."""
    e = html.escape
    children = {}
    for post in panel["posts"]:
        children.setdefault(post.get("reply_to"), []).append(post)

    def render(post, depth):
        badge = e(post["kind"]) + (f" &middot; {e(post['severity'])}" if "severity" in post else "") + \
            (f" &middot; {e(post['stance'])}" if "stance" in post else "")
        refs = " ".join(f"<code>{e(r)}</code>" for r in post["refs"])
        out = [f'<div class="post {e(post["kind"])}" id="{e(post["id"])}" style="margin-left:{min(depth, 4) * 1.5}rem">'
               f'<div class="meta"><b>{e(post["author"])}</b> <span class="badge">{badge}</span> '
               f'<span class="id">{e(post["id"])} &middot; round {post["round"]}</span></div>'
               f'<p>{e(post["text"])}</p><div class="refs">{refs}</div></div>']
        for child in children.get(post["id"], []):
            out.extend(render(child, depth + 1))
        return out

    sections = []
    for run in panel["runs"]:
        name = run["researcher"]
        s = panel["synthesis"]["runs"][name]
        rows = "".join(f"<tr><td><a href='#{e(c['post'])}'>{e(c['post'])}</a></td><td>{e(c['author'])}</td>"
                       f"<td>{e(c['severity'])}</td><td>{e('conceded' if c['post'] in s['conceded'] else c['status'])}"
                       f"</td></tr>" for c in s["concerns"])
        thread = [line for post in children.get(None, []) if post["target"] == name for line in render(post, 0)]
        sections.append(f"""<section><h2>{e(name)}'s run</h2>
<div class="muted">{e(run["question"])} &middot; manifest <code>{e(run["manifest_digest"][:16])}</code></div>
<p class="verdict">{e(s["verdict"].replace("_", " "))}</p>
<table><tr><th>Concern</th><th>Raised by</th><th>Severity</th><th>Status</th></tr>{rows}</table>
{"".join(thread) or "<p class='muted'>No critiques.</p>"}</section>""")
    rejected = "".join(f"<li>{e(r['author'])}, round {r['round']}: {e(r['reason'])}</li>" for r in panel["rejected"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sewall.ai critique panel</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d232a; --muted:#5d6670; --line:#d9d6cf; --card:#fff;
  --concern:#b4532a; --support:#2f7d4f; --question:#2f5d9a; --limitation:#7a6a2f; --reply:#5d6670; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16191d; --fg:#e6e3dd; --muted:#9aa3ad; --line:#333a42;
  --card:#1e2227; --concern:#e08a5f; --support:#6fbf8c; --question:#7fa7e0; --limitation:#cdb969; --reply:#9aa3ad; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0 auto;
  max-width:860px; padding:24px 16px; }}
h1 {{ font-size:1.4rem; margin:0 0 .3rem; }} .muted, .id {{ color:var(--muted); font-size:.85rem; }}
section {{ border-top:1px solid var(--line); margin-top:1.5rem; }}
.post {{ background:var(--card); border:1px solid var(--line); border-left:4px solid var(--reply);
  border-radius:6px; padding:.6rem .8rem; margin:.6rem 0; }}
.post.concern {{ border-left-color:var(--concern); }} .post.support {{ border-left-color:var(--support); }}
.post.question {{ border-left-color:var(--question); }} .post.limitation {{ border-left-color:var(--limitation); }}
.post p {{ margin:.3rem 0; }} .badge {{ font-size:.8rem; color:var(--muted); }}
.refs code, code {{ font-size:.75rem; overflow-wrap:anywhere; }}
.verdict {{ font-weight:600; font-size:1.1rem; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }} td, th {{ border-bottom:1px solid var(--line);
  padding:.3rem; text-align:left; }}
a {{ color:var(--question); }}
</style></head><body>
<h1>Cross-run critique panel</h1>
<div class="muted">{e(panel["topic"])} &middot; panel <code>{e(panel["content_digest"][:16])}</code> &middot;
panelists {e(", ".join(r["researcher"] for r in panel["runs"]))}. Biological claims validated: no.</div>
{"".join(sections)}
<h2>Rejected posts</h2>
<ul>{rejected or "<li>None</li>"}</ul>
<h2>Limitations</h2>
<ul>{"".join(f"<li>{e(x)}</li>" for x in panel["limitations"])}</ul>
</body></html>
"""
