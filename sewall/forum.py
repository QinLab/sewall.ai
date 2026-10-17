"""Agentic peer review: a bounded discussion forum over a finished, verified run.

Reviewer agents read a compact view of a recorded manifest and post concerns,
support, questions and limitations in round 1, then reply in round 2 by
answering, agreeing with or disputing earlier posts. A deterministic moderator
closes round 3 with a synthesis: which concerns were resolved, which stay open
or contested, and a verdict on the run as retrieved evidence.

Every post must cite graph nodes or earlier posts that exist. Invalid posts are
rejected and recorded with a reason, never silently dropped. Posts are
hash-chained from the manifest content digest, so the forum is bound to one
exact run and any later edit is detectable. The forum never validates a
biological claim; it reviews what the run retrieved and how.

Rule reviewers make no model calls. A model reviewer returns posts in the same
structure and passes the same checks.
"""

from copy import deepcopy
import html
import re

from .agent import verify_agent_manifest
from .graph import digest

SCHEMA_VERSION = "0.1.0"
KINDS = ("concern", "support", "question", "limitation", "reply")
SEVERITIES = ("minor", "major")
STANCES = ("agree", "dispute", "answer")
DRAFT_FIELDS = {"kind", "text", "refs", "severity", "reply_to", "stance", "topic"}
MAX_TEXT = 1000
MAX_REFS = 12
MAX_POSTS_PER_TURN = 6
MAX_REVIEWERS = 8
ROUNDS = (1, 2)
VERDICTS = ("insufficient_evidence", "needs_revision", "acceptable_as_retrieved_evidence")
_TOPIC = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_LOCAL = re.compile(r"\b(chesapeake|virginia|usa|york river|maryland)\b", re.IGNORECASE)
_BLOOM = re.compile(r"\bblooms?\b", re.IGNORECASE)
_ANCHORS = {"question", "result"}


def manifest_view(manifest: dict) -> dict:
    """Return the compact, bounded facts a reviewer sees about one run."""
    observations, failed = [], []
    for item in manifest["actions"]:
        obs = item.get("observation") or {}
        request = item["action"]
        if item["status"] != "completed":
            failed.append({"index": item["index"], "node": f"action:{item['index']}",
                           "action": request.get("action"), "status": item["status"],
                           "error": str(obs.get("error", ""))[:300]})
            continue
        node = obs.get("summary_node") or obs.get("inventory_node")
        if request.get("action") == "search":
            observations.append({"node": f"action:{item['index']}", "operation": "search",
                                 "database": obs.get("database"), "query": obs.get("query"),
                                 "total": obs.get("total"), "record_ids": obs.get("record_ids", [])})
        elif obs.get("operation") == "chlorophyll_timeseries":
            observations.append({"node": node, "operation": "chlorophyll_timeseries", "site": obs["site"],
                                 "start": obs["start"], "end": obs["end"],
                                 "scenes_with_water_pixels": obs["scenes_with_water_pixels"],
                                 "ndci_range": obs.get("ndci_range")})
        elif obs.get("operation") == "alphaearth_context":
            observations.append({"node": node, "operation": "alphaearth_context", "site": obs["site"],
                                 "year": obs["year"], "scale_meters": obs["scale_meters"]})
        elif "inventory_node" in obs:
            observations.append({"node": node, "operation": "taxon_inventory", "taxon": obs["taxon"],
                                 "total": obs["total"], "tsa_records": obs["tsa_records"],
                                 "countries": [s.get("qualifiers", {}).get("country", "")
                                               for s in obs.get("sample", [])]})
        elif node:
            observations.append({"node": node, "operation": obs.get("operation", request.get("action"))})
    summary = manifest.get("metadata_summary") or {}
    return {"question": manifest["question"], "status": manifest["status"],
            "stop_reason": manifest["stop_reason"], "content_digest": manifest["content_digest"],
            "records": [{"id": r["id"], "database": r["database"], "title": r["title"][:200]}
                        for r in manifest["records"]],
            "observations": observations, "failed_actions": failed,
            "policies": [{"source_id": p["source_id"], "status": p["status"]} for p in manifest["policies"]],
            "review_summary": {"summary": str(summary.get("summary", ""))[:500],
                               "record_ids": list(summary.get("record_ids", []))},
            "node_ids": sorted(n["id"] for n in manifest["graph"]["nodes"])}


def _post(author, kind, text, refs, **extra):
    return {"kind": kind, "text": text, "refs": list(refs), **extra}


def _find(thread, author=None, topic=None, kind=None):
    return [p for p in thread if (author is None or p["author"] == author)
            and (topic is None or p.get("topic") == topic) and (kind is None or p["kind"] == kind)]


def _anchor(view):
    """The run's result node, or the question node when the run stopped before a result."""
    return "result" if "result" in view["node_ids"] else "question"


def _obs(view, operation):
    return [o for o in view["observations"] if o["operation"] == operation]


def methods_reviewer(view, round_, thread):
    """Provenance and completeness of the run itself."""
    posts = []
    if round_ == 1:
        if view["status"] != "completed":
            posts.append(_post("methods", "concern", f"The run stopped with status {view['status']} "
                               f"({view['stop_reason']}); its evidence is partial.", [_anchor(view)],
                               severity="major", topic="run-incomplete"))
        for item in view["failed_actions"]:
            posts.append(_post("methods", "concern", f"Action {item['index']} ({item['action']}) ended "
                               f"{item['status']}: {item['error'] or 'no error recorded'}.", [item["node"]],
                               severity="major" if item["action"] == "search" else "minor",
                               topic=f"failed-action-{item['index']}"))
        cited = set(view["review_summary"]["record_ids"])
        unreviewed = [o["node"] for o in view["observations"]
                      if o["operation"] not in ("search", "taxon_inventory") and o["node"] not in cited]
        if unreviewed:
            posts.append(_post("methods", "concern", f"The run's metadata review covers literature records "
                               f"only; {len(unreviewed)} computed observations (satellite or sensor summaries) "
                               "were never reviewed.", ([r for r in ("review",) if r in view["node_ids"]] + unreviewed)[:MAX_REFS],
                               severity="minor", topic="review-omits-observations"))
        if view["status"] == "completed":
            posts.append(_post("methods", "support", "Every action, observation and policy decision is a "
                               "node in a digest-verified graph, so the run can be replayed and audited.",
                               ["question", _anchor(view)], topic="verified-chain"))
        if _obs(view, "chlorophyll_timeseries"):
            posts.append(_post("methods", "question", "How many usable water scenes support each monthly "
                               "NDCI value, and are any months too sparse to compare?",
                               [o["node"] for o in _obs(view, "chlorophyll_timeseries")][:MAX_REFS],
                               topic="scene-counts"))
    else:
        for post in thread:
            if post["kind"] == "support" and post["author"] != "methods" and not any(
                    r not in ("question", "result") and not r.startswith("post:") for r in post["refs"]):
                posts.append(_post("methods", "reply", "This support cites no record or observation, so it "
                                   "cannot be checked against the run.", [post["id"]],
                                   reply_to=post["id"], stance="dispute"))
    return posts


def ncbi_reviewer(view, round_, thread):
    """Literature and sequence records."""
    posts, records = [], view["records"]
    inventories = _obs(view, "taxon_inventory")
    if round_ == 1:
        if records:
            posts.append(_post("ncbi", "support", f"{len(records)} public literature records were retrieved "
                               "and each is a node with its source URL.", [r["id"] for r in records][:MAX_REFS],
                               topic="records-present"))
        elif not inventories:
            posts.append(_post("ncbi", "concern", "No literature or sequence records were retrieved, so "
                               "nothing in the run ties the question to published work.", [_anchor(view)],
                               severity="major", topic="no-records"))
        if records or inventories:
            posts.append(_post("ncbi", "limitation", "Records are metadata only: titles and summaries, no "
                               "full text, sequences or measurements.",
                               [p for p in ("policy:pubmed",) if p in view["node_ids"]] or [_anchor(view)],
                               topic="metadata-only"))
        for inv in inventories:
            countries = [c for c in inv["countries"] if c]
            if countries and not any(_LOCAL.search(c) for c in countries):
                posts.append(_post("ncbi", "concern", f"None of the {len(countries)} sampled sequence records "
                                   f"for {inv['taxon']} lists a Chesapeake, Virginia or USA origin "
                                   f"({'; '.join(sorted(set(countries)))[:200]}); genotypes may not match "
                                   "the local population.", [inv["node"]], severity="minor",
                                   topic="sequence-origin"))
    else:
        bloom_records = [r["id"] for r in records if _BLOOM.search(r["title"])]
        for post in _find(thread, author="ecology", topic="no-in-situ"):
            if bloom_records:
                posts.append(_post("ncbi", "reply", f"{len(bloom_records)} retrieved paper titles mention a "
                                   "bloom; those papers may hold in situ observations, but the run reads only "
                                   "their metadata.", [post["id"], *bloom_records][:MAX_REFS],
                                   reply_to=post["id"], stance="answer"))
    return posts


def earthengine_reviewer(view, round_, thread):
    """Satellite summaries."""
    posts, chl, alpha = [], _obs(view, "chlorophyll_timeseries"), _obs(view, "alphaearth_context")
    sparse = [o for o in chl if o["scenes_with_water_pixels"] < 3]
    if round_ == 1:
        for o in sparse:
            posts.append(_post("earthengine", "concern", f"{o['start'][:7]} has only "
                               f"{o['scenes_with_water_pixels']} usable water scenes; a monthly NDCI value "
                               "from so few scenes is fragile.", [o["node"]], severity="minor",
                               topic=f"sparse-{o['start'][:7]}"))
        if chl:
            posts.append(_post("earthengine", "limitation", "NDCI is a chlorophyll proxy from red-edge "
                               "reflectance; it cannot identify a species or confirm a bloom.",
                               [chl[0]["node"]], topic="ndci-proxy"))
        if alpha:
            scales = sorted({o["scale_meters"] for o in alpha})
            posts.append(_post("earthengine", "limitation", "AlphaEarth features are learned embeddings, not "
                               f"physical measurements; they were reduced at {', '.join(map(str, scales))} m.",
                               [o["node"] for o in alpha][:MAX_REFS], topic="alphaearth-learned"))
    else:
        for question in _find(thread, topic="scene-counts", kind="question"):
            counts = ", ".join(f"{o['start'][:7]}: {o['scenes_with_water_pixels']}" for o in chl)
            note = (f" {len(sparse)} of {len(chl)} months fall below 3 scenes." if sparse
                    else " Every month has at least 3 scenes.")
            posts.append(_post("earthengine", "reply", f"Usable water scenes per month: {counts}.{note}",
                               [question["id"], *(o["node"] for o in chl)][:MAX_REFS],
                               reply_to=question["id"], stance="answer"))
        for concern in thread:
            if (concern["kind"] == "concern" and (concern.get("topic") or "").startswith("failed-action")
                    and any(o["node"] in concern["refs"] for o in view["failed_actions"]
                            if o["action"] in ("chlorophyll_timeseries", "alphaearth_context"))):
                posts.append(_post("earthengine", "reply", "Confirmed: a failed satellite request leaves a gap, "
                                   "and a gap is not a negative observation.", [concern["id"]],
                                   reply_to=concern["id"], stance="agree"))
        for concern in _find(thread, author="ecology", topic="no-in-situ"):
            answers = [p["id"] for p in thread if p.get("reply_to") == concern["id"] and p.get("stance") == "answer"]
            posts.append(_post("earthengine", "reply", "Agreed: paper metadata may name a bloom, but no cell count "
                               "or species observation is linked to any satellite scene, so NDCI changes cannot "
                               "be attributed to the organism.", [concern["id"], *answers][:MAX_REFS],
                               reply_to=concern["id"], stance="agree"))
    return posts


def ecology_reviewer(view, round_, thread):
    """Whether the evidence can speak to the biological question."""
    if not _BLOOM.search(view["question"]):
        return []
    if round_ == 1:
        return [_post("ecology", "concern", "The run holds no in situ cell count or species observation for "
                      "the bloom, so the satellite signal cannot be attributed to the named organism.",
                      ["question"], severity="major", topic="no-in-situ")]
    return []


RULE_REVIEWERS = (("methods", methods_reviewer), ("ncbi", ncbi_reviewer),
                  ("earthengine", earthengine_reviewer), ("ecology", ecology_reviewer))

MODEL_PROMPT = """You are a peer reviewer in a Sewall.ai review forum. You review one recorded research run
for what it retrieved and how; you never validate or assert a biological claim.
Return only JSON: {"posts": [...]}, at most 6 posts. Each post has:
  kind: concern | support | question | limitation | reply
  text: plain text, at most 1000 characters
  refs: 1 to 12 ids, each a node id from run.node_ids or an earlier post id from the thread
  severity: minor | major (concerns only)
  reply_to and stance (agree | dispute | answer): replies only; reply_to is an earlier post id by another author
  topic: optional short slug
In round 1 post your own review. In round 2 reply to earlier posts. Cite only what the run contains."""


class ModelReviewer:
    """A reviewer backed by a model client's complete_json."""

    def __init__(self, name, client, focus="general methods and evidence"):
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ValueError("reviewer name must be a short lowercase slug")
        self.name, self.client, self.focus = name, client, str(focus)[:300]

    def __call__(self, view, round_, thread):
        payload = {"reviewer": self.name, "focus": self.focus, "round": round_, "run": view,
                   "thread": [{k: p.get(k) for k in ("id", "author", "kind", "severity", "reply_to",
                                                    "stance", "topic", "text", "refs")} for p in thread]}
        response = self.client.complete_json(MODEL_PROMPT, payload)
        self.last_call = {k: response.get(k) for k in ("provider", "model", "model_version", "request_id",
                                                       "response_sha256", "usage", "latency_seconds")}
        output = response.get("output")
        if not isinstance(output, dict) or not isinstance(output.get("posts"), list):
            raise ValueError("model reviewer output must be an object with a posts list")
        return output["posts"]


def check_draft(draft, author, round_, known, thread):
    """Return a normalized post body or raise ValueError with the reason."""
    if not isinstance(draft, dict) or not set(draft) <= DRAFT_FIELDS:
        raise ValueError("post must be an object with only the allowed fields")
    kind, text, refs = draft.get("kind"), draft.get("text"), draft.get("refs")
    if kind not in KINDS:
        raise ValueError("unknown post kind")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT:
        raise ValueError(f"text must be 1 to {MAX_TEXT} characters")
    if not isinstance(refs, list) or not 1 <= len(refs) <= MAX_REFS or len(set(map(str, refs))) != len(refs):
        raise ValueError(f"refs must list 1 to {MAX_REFS} distinct ids")
    posts = {p["id"]: p for p in thread}
    for ref in refs:
        if not isinstance(ref, str) or (ref not in known and ref not in posts):
            raise ValueError(f"ref {str(ref)[:80]!r} is not a node in the run or an earlier post")
    body = {"kind": kind, "text": text.strip(), "refs": refs}
    if kind == "concern":
        if draft.get("severity") not in SEVERITIES:
            raise ValueError("a concern needs severity minor or major")
        body["severity"] = draft["severity"]
    elif "severity" in draft:
        raise ValueError("only concerns carry a severity")
    if kind == "reply":
        target = posts.get(draft.get("reply_to"))
        if round_ == 1:
            raise ValueError("replies open in round 2")
        if target is None or target["author"] == author:
            raise ValueError("reply_to must name an earlier post by another reviewer")
        if draft.get("stance") not in STANCES:
            raise ValueError("a reply needs stance agree, dispute or answer")
        body.update(reply_to=target["id"], stance=draft["stance"])
    elif "reply_to" in draft or "stance" in draft:
        raise ValueError("only replies carry reply_to and stance")
    if draft.get("topic") is not None:
        if not isinstance(draft["topic"], str) or not _TOPIC.fullmatch(draft["topic"]):
            raise ValueError("topic must be a short lowercase slug")
        body["topic"] = draft["topic"]
    return body


def new_evidence(reply, concern, view):
    """Run nodes a reply cites that the concern did not; posts and the question or result anchors do not count."""
    nodes = set(view["node_ids"]) - _ANCHORS
    return sorted(set(reply["refs"]) & nodes - set(concern["refs"]))


def synthesize(view, posts):
    """Deterministic moderator: classify each concern and give the verdict.

    An answer or dispute resolves a concern only when it cites run evidence the
    concern did not already cite. An agreement with a resolved concern makes it
    contested. A reply without new evidence leaves the concern open.
    """
    concerns = []
    for post in posts:
        if post["kind"] != "concern":
            continue
        replies = [p for p in posts if p.get("reply_to") == post["id"] and p["author"] != post["author"]]
        answered = any(p["stance"] in ("answer", "dispute") and new_evidence(p, post, view) for p in replies)
        agreed = any(p["stance"] == "agree" for p in replies)
        status = "contested" if answered and agreed else "resolved" if answered else "open"
        concerns.append({"post": post["id"], "author": post["author"], "severity": post["severity"],
                         "status": status, "replies": [p["id"] for p in replies]})
    disputed = sorted({p["reply_to"] for p in posts if p.get("stance") == "dispute"})
    evidence = bool(view["records"]) or any(o["operation"] != "search" for o in view["observations"])
    blocking = [c["post"] for c in concerns if c["severity"] == "major" and c["status"] != "resolved"]
    if view["status"] != "completed" or not evidence:
        verdict = "insufficient_evidence"
    elif blocking:
        verdict = "needs_revision"
    else:
        verdict = "acceptable_as_retrieved_evidence"
    counts = {s: sum(c["status"] == s for c in concerns) for s in ("open", "contested", "resolved")}
    return {"verdict": verdict, "concerns": concerns, "blocking_concerns": blocking,
            "disputed_posts": disputed, "concern_counts": counts,
            "limitations": [p["id"] for p in posts if p["kind"] == "limitation"],
            "biological_claims_validated": False}


def _chain(posts, binding):
    prev = binding
    for post in posts:
        post["prev"] = prev
        post["hash"] = digest({k: v for k, v in post.items() if k != "hash"})
        prev = post["hash"]


def run_forum(manifest: dict, topic=None, reviewers=None) -> dict:
    """Run two discussion rounds and a moderator synthesis over a verified manifest."""
    check = verify_agent_manifest(manifest)
    if not check.get("valid"):
        raise ValueError("Manifest failed verification; the forum reviews only verified runs")
    reviewers = list(reviewers) if reviewers is not None else list(RULE_REVIEWERS)
    names = [name for name, _ in reviewers]
    if not 1 <= len(reviewers) <= MAX_REVIEWERS or len(set(names)) != len(names) or not all(
            isinstance(n, str) and _NAME.fullmatch(n) for n in names):
        raise ValueError(f"forum needs 1 to {MAX_REVIEWERS} reviewers with distinct slug names")
    view = manifest_view(manifest)
    known = set(view["node_ids"])
    posts, rejected, calls = [], [], []
    for round_ in ROUNDS:
        for name, reviewer in reviewers:
            try:
                drafts = reviewer(view, round_, deepcopy(posts))
                if not isinstance(drafts, list):
                    raise ValueError("reviewer must return a list of posts")
                status = "completed"
            except Exception as exc:  # A failing reviewer is recorded, not fatal.
                drafts, status = [], f"failed ({type(exc).__name__})"
            if isinstance(reviewer, ModelReviewer):
                calls.append({"author": name, "round": round_, "status": status,
                              **getattr(reviewer, "last_call", {})})
                reviewer.last_call = {}
            elif status != "completed":
                rejected.append({"author": name, "round": round_, "reason": f"reviewer {status}",
                                 "draft_sha256": None})
            for position, draft in enumerate(drafts):
                try:
                    if position >= MAX_POSTS_PER_TURN:
                        raise ValueError(f"more than {MAX_POSTS_PER_TURN} posts in one turn")
                    body = check_draft(draft, name, round_, known, posts)
                except ValueError as exc:
                    rejected.append({"author": name, "round": round_, "reason": str(exc)[:300],
                                     "draft_sha256": digest(draft) if _jsonable(draft) else None})
                    continue
                posts.append({"id": f"post:{len(posts) + 1}", "round": round_, "author": name, **body})
    synthesis = synthesize(view, posts)
    rule_only = all(not isinstance(r, ModelReviewer) for _, r in reviewers)
    forum = {"schema_version": SCHEMA_VERSION, "kind": "sewall_review_forum",
             "manifest_digest": manifest["content_digest"], "question": manifest["question"],
             "topic": str(topic)[:300] if topic else manifest["question"],
             "reviewers": [{"name": n, "type": "model" if isinstance(r, ModelReviewer) else "rule",
                            "focus": getattr(r, "focus", None) or (r.__doc__ or "").strip()}
                           for n, r in reviewers],
             "rule_only": rule_only, "posts": posts, "rejected": rejected, "model_calls": calls,
             "synthesis": {"id": f"post:{len(posts) + 1}", "round": ROUNDS[-1] + 1, "author": "moderator",
                           "kind": "synthesis", **synthesis},
             "limitations": ["The forum reviews retrieved metadata and computed summaries; it validates no "
                             "biological claim.",
                             "Rule reviewers apply fixed checks; agreement among them is not independent "
                             "expert judgment.",
                             "Model reviewer posts are checked for structure and citations, not for truth."]}
    _chain(posts, forum["manifest_digest"])
    forum["synthesis"]["prev"] = posts[-1]["hash"] if posts else forum["manifest_digest"]
    forum["content_digest"] = digest({k: v for k, v in forum.items() if k != "content_digest"})
    return forum


def _jsonable(value):
    try:
        digest(value)
        return True
    except (TypeError, ValueError):
        return False


def verify_forum(forum: dict, manifest: dict) -> dict:
    """Check the binding, hash chain, citations and synthesis without any model call."""
    try:
        if not isinstance(forum, dict) or forum.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported forum")
        if digest({k: v for k, v in forum.items() if k != "content_digest"}) != forum.get("content_digest"):
            raise ValueError("Forum content digest mismatch")
        if not verify_agent_manifest(manifest).get("valid"):
            raise ValueError("Manifest failed verification")
        if forum["manifest_digest"] != manifest["content_digest"]:
            raise ValueError("Forum is bound to a different manifest")
        view = manifest_view(manifest)
        known, prev, earlier = set(view["node_ids"]), forum["manifest_digest"], []
        for number, post in enumerate(forum["posts"], 1):
            if post.get("id") != f"post:{number}" or post.get("prev") != prev:
                raise ValueError(f"Broken post chain at post {number}")
            if digest({k: v for k, v in post.items() if k != "hash"}) != post.get("hash"):
                raise ValueError(f"Post {number} hash mismatch")
            body = {k: post[k] for k in DRAFT_FIELDS if k in post}
            check_draft(body, post["author"], post["round"], known, earlier)
            prev = post["hash"]
            earlier.append(post)
        expected = synthesize(view, forum["posts"])
        if {k: v for k, v in forum["synthesis"].items() if k in expected} != expected or \
                forum["synthesis"].get("prev") != prev:
            raise ValueError("Moderator synthesis does not match the posts")
        if forum["rule_only"]:
            rules = dict(RULE_REVIEWERS)
            rebuilt = run_forum(manifest, forum["topic"], [(x["name"], rules[x["name"]]) for x in forum["reviewers"]])
            if rebuilt["content_digest"] != forum["content_digest"]:
                raise ValueError("Rule-only forum does not rebuild to the same digest")
        return {"valid": True, "posts": len(forum["posts"]), "verdict": forum["synthesis"]["verdict"],
                "content_digest": forum["content_digest"]}
    except (KeyError, TypeError, ValueError) as exc:
        return {"valid": False, "reason": str(exc)[:300]}


def render_forum_html(forum: dict) -> str:
    """Self-contained HTML page for a forum, threaded by reply."""
    e = html.escape
    children = {}
    for post in forum["posts"]:
        children.setdefault(post.get("reply_to"), []).append(post)

    def render(post, depth):
        badge = post["kind"] + (f" &middot; {e(post['severity'])}" if "severity" in post else "") + \
            (f" &middot; {e(post['stance'])}" if "stance" in post else "")
        refs = " ".join(f"<code>{e(r)}</code>" for r in post["refs"])
        out = [f'<div class="post {e(post["kind"])}" id="{e(post["id"])}" style="margin-left:{min(depth, 4) * 1.5}rem">'
               f'<div class="meta"><b>{e(post["author"])}</b> <span class="badge">{badge}</span> '
               f'<span class="id">{e(post["id"])} &middot; round {post["round"]}</span></div>'
               f'<p>{e(post["text"])}</p><div class="refs">{refs}</div></div>']
        for child in children.get(post["id"], []):
            out.extend(render(child, depth + 1))
        return out

    body = [line for post in children.get(None, []) for line in render(post, 0)]
    s = forum["synthesis"]
    rows = "".join(f"<tr><td><a href='#{e(c['post'])}'>{e(c['post'])}</a></td><td>{e(c['author'])}</td>"
                   f"<td>{e(c['severity'])}</td><td>{e(c['status'])}</td></tr>" for c in s["concerns"])
    rejected = "".join(f"<li>{e(r['author'])}, round {r['round']}: {e(r['reason'])}</li>" for r in forum["rejected"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sewall.ai review forum</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d232a; --muted:#5d6670; --line:#d9d6cf; --card:#fff;
  --concern:#b4532a; --support:#2f7d4f; --question:#2f5d9a; --limitation:#7a6a2f; --reply:#5d6670; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16191d; --fg:#e6e3dd; --muted:#9aa3ad; --line:#333a42;
  --card:#1e2227; --concern:#e08a5f; --support:#6fbf8c; --question:#7fa7e0; --limitation:#cdb969; --reply:#9aa3ad; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0 auto;
  max-width:860px; padding:24px 16px; }}
h1 {{ font-size:1.4rem; margin:0 0 .3rem; }} .muted, .id {{ color:var(--muted); font-size:.85rem; }}
.post {{ background:var(--card); border:1px solid var(--line); border-left:4px solid var(--reply);
  border-radius:6px; padding:.6rem .8rem; margin:.6rem 0; }}
.post.concern {{ border-left-color:var(--concern); }} .post.support {{ border-left-color:var(--support); }}
.post.question {{ border-left-color:var(--question); }} .post.limitation {{ border-left-color:var(--limitation); }}
.post p {{ margin:.3rem 0; }} .badge {{ font-size:.8rem; color:var(--muted); }}
.refs code {{ font-size:.75rem; overflow-wrap:anywhere; }}
.verdict {{ font-weight:600; font-size:1.1rem; }}
table {{ border-collapse:collapse; width:100%; font-size:.9rem; }} td, th {{ border-bottom:1px solid var(--line);
  padding:.3rem; text-align:left; }}
a {{ color:var(--question); }}
</style></head><body>
<h1>Agentic peer review</h1>
<div class="muted">{e(forum["question"])}</div>
<div class="muted">Manifest <code>{e(forum["manifest_digest"][:16])}</code> &middot; forum
<code>{e(forum["content_digest"][:16])}</code> &middot; reviewers {e(", ".join(r["name"] for r in forum["reviewers"]))}</div>
<h2>Moderator synthesis</h2>
<p class="verdict">{e(s["verdict"].replace("_", " "))}</p>
<p class="muted">Concerns: {s["concern_counts"]["open"]} open, {s["concern_counts"]["contested"]} contested,
{s["concern_counts"]["resolved"]} resolved. Biological claims validated: no.</p>
<table><tr><th>Concern</th><th>Author</th><th>Severity</th><th>Status</th></tr>{rows}</table>
<h2>Discussion</h2>
{"".join(body)}
<h2>Rejected posts</h2>
<ul>{rejected or "<li>None</li>"}</ul>
<h2>Limitations</h2>
<ul>{"".join(f"<li>{e(x)}</li>" for x in forum["limitations"])}</ul>
</body></html>
"""
