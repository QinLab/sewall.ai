"""Dataset-expert agents and a lab manager that routes a question among them.

Each expert holds what a curator of one source knows: names and synonyms, what
the source can and cannot show, request limits, and the Skills that reach it.
An expert reads a parsed question and proposes actions on registered Skills
with its cautions and gaps. Experts never execute anything. The lab manager
orders and caps the proposals and writes them as a fixed script, which the
bounded controller checks and records exactly as it checks a model's plan.

The growth-model expert maps a growth question onto the terms of the planned
model, dN/dt = mu_max f_T f_S f_I f_N N - losses, and proposes the genotype,
pathway, environmental-response and community searches that inform each term.
For a taxon with a curated ribotype panel it also fetches the marker sequences
and assigns them to published ribotypes.

This draft uses deterministic rules and makes no model calls. A model-backed
expert would return the same consultation structure.
"""

from datetime import date
import re

from . import earthengine
from .ribotype import PANELS
from .strains import TABLES
from .scripted import MAX_STEPS, load_script


# Accepted names first; synonyms a curator would search alongside them.
TAXA = {
    "Margalefidinium polykrikoides": ("Cochlodinium polykrikoides",),
    "Alexandrium monilatum": ("Gonyaulax monilata",),
    "Zostera marina": (),
}
PLACES = {"york": "york-river-lower", "lafayette": "lafayette-river"}
PLACE_TERMS = {"york-river-lower": "York River", "lafayette-river": "Lafayette River"}
SEASON = ("06-01", "10-31")
_DATE = re.compile(r"\b(20[0-9]{2}-[0-9]{2}-[0-9]{2})\b")
_YEAR = re.compile(r"\b(20[0-9]{2})\b")


# Environmental factor: trigger stems, model term it informs, response terms, pathway terms.
FACTORS = {
    "temperature": (("temperat", "thermal", "heat", "warm"), "f_T(T): Topt, Tmin, Tmax",
                    "(temperature OR thermal)", '("heat shock" OR HSP70 OR "thermal stress")'),
    "salinity": (("salin",), "f_S(S): lower and upper salinity bounds",
                 "salinity", "(osmotic OR osmolyte OR DMSP)"),
    "light": (("light", "irradian", "shad", "migrat"), "f_I(I): initial slope, saturation, photoinhibition",
              "(light OR irradiance)", '(photosynthesis OR "light harvesting" OR rubisco)'),
    "nutrients": (("nutrient", "nitrogen", "nitrate", "ammonium", "urea", "phosph"),
                  "f_N: Monod half-saturation or Droop minimum quota",
                  "(nitrogen OR phosphorus OR nutrient)",
                  '("nitrate reductase" OR urease OR "nitrogen assimilation" OR "alkaline phosphatase")'),
    "community": (("communit", "graz", "predat", "competit", "allelopath", "bacteri", "microbio", "virus",
                   "parasit", "interact", "mixotroph"),
                  "losses and interactions: grazing, competition coefficients, allelopathy, bacterial effects",
                  "(grazing OR competition OR allelopathy OR bacteria OR mixotrophy)", None),
}
DEFAULT_FACTORS = ("temperature", "salinity")
# Findings from the published growth literature that change how a term is fitted.
FACTOR_CAUTIONS = {
    "temperature": "Apply f_T to the sub-daily temperature series and average growth per day; f_T of the daily "
                   "mean is biased by nonlinear averaging (Bernhardt et al. 2018).",
    "light": "Light limitation lowers the thermal optimum by about 5 C across species (Edwards et al. 2016); "
             "record the light level of each source experiment.",
    "community": "Copepod grazing collapses at bloom density in the lower York River (Corson et al. 2024), so a "
                 "constant loss rate m is wrong; M. polykrikoides is also mixotrophic, which roughly doubles "
                 "mu_max (Jeong et al. 2004).",
}


def _has(text, *words):
    return any(re.search(r"\b" + word, text) for word in words)


def parse_question(question: str, today=None) -> dict:
    """Pull taxa, a named site and a date window out of free text. Unmatched parts stay empty."""
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 1000:
        raise ValueError("question must be 1 to 1000 characters")
    text = question.lower()
    taxa = [name for name, synonyms in TAXA.items()
            if any(term.lower() in text or term.split()[-1].lower() in text for term in (name, *synonyms))]
    site = next((value for key, value in PLACES.items() if key in text), None)
    today = today or date.today()
    dates, years = _DATE.findall(question), sorted({int(y) for y in _YEAR.findall(question)})
    window = None
    if len(dates) >= 2:
        window = (dates[0], dates[1])
    elif years and years[-1] < today.year:
        window = (f"{years[-1]}-{SEASON[0]}", f"{years[-1]}-{SEASON[1]}")
    return {"question": question.strip(), "taxa": taxa, "site": site, "window": window, "text": text}


def _months(start, end):
    """Split an inclusive window into calendar months so each call stays under the scene cap."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    spans, cursor = [], first
    while cursor <= last:
        following = date(cursor.year + cursor.month // 12, cursor.month % 12 + 1, 1)
        stop = min(last, date.fromordinal(following.toordinal() - 1))
        spans.append((cursor.isoformat(), stop.isoformat()))
        cursor = following
    return spans


def ncbi_expert(parsed: dict) -> dict:
    text, taxa = parsed["text"], parsed["taxa"]
    relevant = bool(taxa) or _has(text, "sequenc", "genom", "strain", "literature", "paper", "publish", "geo\\b")
    actions, cautions = [], ["Search accepted names with their synonyms; older records use superseded genera."]
    if relevant and taxa:
        names = " OR ".join(term for name in taxa for term in (name, *TAXA[name]))
        place = PLACE_TERMS.get(parsed["site"])
        actions.append({"action": "search", "database": "pubmed",
                        "query": f"({names}) AND {place}" if place else f"({names})",
                        "reason": "Literature that documents the organism and, where named, the place"})
        for name in taxa:
            actions.append({"action": "taxon_inventory", "taxon": name,
                            "reason": f"Public sequence records deposited for {name}"})
        if _has(text, "sequenc", "genom", "strain", "project"):
            actions.append({"action": "search", "database": "bioproject", "query": names,
                            "reason": "Sequencing projects that may hold strain-level data"})
        cautions.append("One transcriptome assembly can add more than 100,000 TSA records; compare counts without TSA.")
    gaps = [] if taxa or not relevant else ["No known taxon was named; the NCBI expert proposes no organism search."]
    return {"expert": "ncbi", "source": "NCBI PubMed, GEO, BioProject and GenBank", "relevant": relevant,
            "actions": actions, "cautions": cautions if relevant else [], "gaps": gaps}


def eol_expert(parsed: dict) -> dict:
    relevant = _has(parsed["text"], "trait", "life histor", "body size", "habitat", "organism", "eol")
    return {"expert": "eol", "source": "Encyclopedia of Life (EOL) and TraitBank", "relevant": relevant,
            "actions": [{"action": "assess_source", "source": "eol",
                         "reason": "Record that trait evidence is wanted and that no EOL connector is configured"}] if relevant else [],
            "cautions": ["EOL trait records aggregate many providers; each value keeps its own citation and units."] if relevant else [],
            "gaps": ["No live EOL Skill exists yet; the Year 1 EOL interface Skill is planned with Georgia Tech."] if relevant else []}


def earthengine_expert(parsed: dict) -> dict:
    text, site, window = parsed["text"], parsed["site"], parsed["window"]
    relevant = _has(text, "satellite", "chlorophyll", "bloom", "landscape", "alphaearth", "imagery", "remote sens")
    actions, gaps = [], []
    if relevant and site is None:
        gaps.append("No named Earth Engine site matched; known sites are " + ", ".join(sorted(earthengine.SITES)) + ".")
    if relevant and site and window is None:
        gaps.append("No completed year or date window was named, so no satellite window was proposed.")
    if relevant and site and window:
        for start, end in _months(*window):
            actions.append({"action": "chlorophyll_timeseries", "site": site, "start": start, "end": end,
                            "reason": f"Water-masked Sentinel-2 NDCI for {start[:7]}"})
        year = int(window[0][:4])
        if _has(text, "landscape", "context", "alphaearth", "compar"):
            actions.append({"action": "alphaearth_context", "site": site, "year": str(year),
                            "reason": "Annual landscape context for the year of the window"})
    cautions = ["NDCI is a chlorophyll proxy; it does not identify a species or confirm a bloom.",
                "A missing or cloudy scene is not a negative observation."] if relevant else []
    return {"expert": "earthengine", "source": "Google Earth Engine: Sentinel-2 and AlphaEarth", "relevant": relevant,
            "actions": actions, "cautions": cautions, "gaps": gaps}


def growth_expert(parsed: dict) -> dict:
    """Plan the evidence a growth or community-interaction model needs for the named taxa."""
    text, taxa = parsed["text"], parsed["taxa"]
    factors = [name for name, (stems, *_rest) in FACTORS.items() if _has(text, *stems)]
    relevant = _has(text, "grow", "pathway", "genotyp", "ribotyp", "gene", "model") or bool(factors and taxa)
    if not relevant:
        return {"expert": "growth", "source": "Growth and community-interaction model inputs", "relevant": False,
                "actions": [], "cautions": [], "gaps": [], "model_terms": []}
    named = factors or list(DEFAULT_FACTORS)
    model_terms = [{"factor": "genotype", "term": "mu_max and the parameters of each response, per ribotype or strain"}]
    model_terms += [{"factor": name, "term": FACTORS[name][1]} for name in named]
    panelled = [name for name in taxa if (name, "lsu") in PANELS]
    tabled = [name for name in panelled if (name, "lsu") in TABLES]
    gaps = [f"No curated ribotype panel exists for {name}; its genotypes cannot be assigned yet."
            for name in taxa if name not in panelled]
    gaps += [f"No curated growth strain table exists for {name}; its ribotypes cannot be paired with growth data yet."
             for name in panelled if name not in tabled]
    gaps += ["No pathway database connector (KEGG, MetaCyc) is configured; pathway evidence comes from literature "
             "and deposited transcriptome projects only.",
             "No environmental forcing connector (CBP, NOAA CO-OPS) is live yet."]
    if not taxa:
        gaps.insert(0, "No known taxon was named; the growth expert proposes no organism search.")
        return {"expert": "growth", "source": "Growth and community-interaction model inputs", "relevant": True,
                "actions": [], "cautions": [], "gaps": gaps, "model_terms": model_terms}
    names = " OR ".join(term for name in taxa for term in (name, *TAXA[name]))
    response = " OR ".join(FACTORS[name][2] for name in named)
    pathways = " OR ".join(FACTORS[name][3] for name in named if FACTORS[name][3]) or FACTORS["temperature"][3]
    actions = [
        {"action": "search", "database": "pubmed", "query": f"({names}) AND growth AND ({response})",
         "reason": "Measured growth responses that parameterize " + ", ".join(named)},
        {"action": "search", "database": "pubmed",
         "query": f"({names}) AND (ribotype OR strain OR LSU OR ITS) AND (growth OR temperature)",
         "reason": "Strain and ribotype differences in growth, for per-genotype parameter distributions"},
        *({"action": "taxon_inventory", "taxon": name,
           "reason": f"LSU and ITS marker records available to assign a genotype for {name}"} for name in taxa),
        *(step for name in panelled for step in (
            {"action": "fetch_markers", "taxon": name, "marker": "lsu",
             "reason": f"Public LSU sequences with isolate, place and date qualifiers for {name}"},
            {"action": "assign_ribotype", "taxon": name, "marker": "lsu",
             "reason": f"Assign the fetched {name} LSU sequences to published ribotypes, abstaining when unclear"})),
        *({"action": "link_strains", "taxon": name, "marker": "lsu",
           "reason": f"Pair growth-studied {name} strains with their ribotype calls for per-genotype parameters"}
          for name in tabled),
        {"action": "search", "database": "pubmed",
         "query": f"({names}) AND (transcriptome OR \"gene expression\") AND ({pathways})",
         "reason": "Pathways and genes whose expression responds to the named factors"},
    ]
    if "community" in named:
        actions.append({"action": "search", "database": "pubmed",
                        "query": f"({names}) AND ({FACTORS['community'][2]})",
                        "reason": "Grazing, competition, allelopathy and bacterial effects for loss and interaction terms"})
    actions.append({"action": "search", "database": "bioproject",
                    "query": f"({names}) AND (transcriptome OR RNA-seq)",
                    "reason": "Deposited transcriptomes that could support pathway annotation"})
    if "community" in named:
        actions.append({"action": "search", "database": "bioproject",
                        "query": f"({names}) AND (microbiome OR 16S OR metagenome OR metabarcoding)",
                        "reason": "Bloom community and microbiome projects for interaction terms"})
    if len(panelled) < len(taxa):
        actions.append({"action": "assess_source", "source": "ncbi_genotype",
                        "reason": "Record that ribotype assignment has no curated panel for "
                                  + ", ".join(name for name in taxa if name not in panelled)})
    cautions = ["Ribotype is a weak predictor of thermal response; strains with identical LSU rDNA differ "
                "(Lim et al. 2021). Use per-strain parameter distributions.",
                "Ribotype panel labels follow the source publications and need a full-text check; unassigned "
                "sequences mark novel variants or regions the panel does not cover.",
                "Gene presence or expression does not give a rate parameter; pathway evidence supports a "
                "mechanism, and growth experiments supply the numbers.",
                "Values measured under different light, temperature and culture protocols are not directly comparable."]
    cautions += [FACTOR_CAUTIONS[name] for name in named if name in FACTOR_CAUTIONS]
    if "community" in named:
        cautions.append("Interaction terms need parameters and observations for every interacting population; "
                        "a single-species proof of concept can only report them as missing.")
    if not factors:
        cautions.append("No environmental factor was named; temperature and salinity were assumed as the default model terms.")
    return {"expert": "growth", "source": "Growth and community-interaction model inputs", "relevant": True,
            "actions": actions, "cautions": cautions, "gaps": gaps, "model_terms": model_terms}


EXPERTS = (ncbi_expert, earthengine_expert, growth_expert, eol_expert)


def _key(action):
    return tuple(sorted((key, value) for key, value in action.items() if key != "reason"))


def _interleave(consultations):
    """Take actions round-robin across experts, in each expert's own order, dropping repeats."""
    queues, merged, seen = [list(item["actions"]) for item in consultations], [], set()
    while any(queues):
        for queue in queues:
            while queue:
                action = queue.pop(0)
                if _key(action) not in seen:
                    seen.add(_key(action))
                    merged.append(action)
                    break
    return merged


def consult(question: str, today=None) -> dict:
    """Ask every expert, then merge their proposals into one capped, ordered script.

    Proposals are taken round-robin so every relevant expert is represented
    before the step cap; identical actions proposed by two experts run once.
    """
    parsed = parse_question(question, today)
    consultations = [expert(parsed) for expert in EXPERTS]
    proposed = _interleave(consultations)
    steps, deferred = proposed[:MAX_STEPS], proposed[MAX_STEPS:]
    gaps = [gap for item in consultations for gap in item["gaps"]]
    if deferred:
        gaps.append(f"{len(deferred)} proposed actions exceeded the {MAX_STEPS}-step script limit and were deferred.")
    script = None
    if steps:
        slug = re.sub(r"[^a-z0-9]+", "-", parsed["text"])[:40].strip("-") or "consult"
        script = load_script({"name": slug, "description": "Plan written by the Sewall.ai lab manager from expert consultations.",
                              "steps": steps, "gaps": gaps[:8]})
    return {"question": parsed["question"],
            "parsed": {key: parsed[key] for key in ("taxa", "site", "window")},
            "consultations": consultations, "deferred": deferred, "script": script,
            "suggested_limits": {"max_actions": len(steps), "max_model_calls": len(steps) + 2} if steps else None}
