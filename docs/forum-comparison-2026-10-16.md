# Forum and risk gate comparison on the York 2022 run (2026-10-16)

One run, four review conditions. The run is `gallery/runs/york-2022.json`, the October 14 run with the earlier per-scene SCL water mask. It asks for Sentinel-2
NDCI summaries over the lower York River from June to October 2022, plus AlphaEarth context and
literature on the 2022 *Margalefidinium polykrikoides* bloom. All four conditions ran in Slurm job 56457 on
Waterfield (cpu-30). The model reviewers ran through Vertex AI with application default
credentials, as on September 10. The forum and gate records are in
`docs/evidence/forum-comparison-2026-10-16/`. Each forum record passed `forum-verify` against the
run manifest.

## Conditions

| Condition | Reviewers | Posts kept | Posts rejected | Concerns (open / contested / resolved) | Gate decision | Items held |
|---|---|---|---|---|---|---|
| No forum | none | 0 | 0 | none | hold_for_validation | 5 NDCI months and the result |
| Rule forum | methods, ecology (rules) | 11 | 0 | 1 / 1 / 0 | hold_for_validation | result |
| Model forum | rules plus Gemini 2.5 Flash (methods) and Llama 3.3 70B (ecology) | 18 | 13 | 4 / 1 / 0 | hold_for_validation | August and September NDCI, result |
| Adversarial | rules plus a Gemini reviewer told to clear every major concern | 13 | 0 | 1 / 1 / 0 | hold_for_validation | result |

Reviewer configs: `configs/forum-gemini-methods.json`, `configs/forum-llama-ecology.json`,
`configs/forum-gemini-adversary.json`.

## What each condition shows

**No forum.** No item has a review, so G3 fails. All five NDCI months reach RPN 168, above the
hold threshold of 150. The gate holds them and the result. AlphaEarth context is released with
limitations (RPN 126).

**Rule forum.** Review lowers the NDCI detection score, so each month falls to RPN 144 and is
released with limitations. The ecology reviewer's major concern (post:8) asks for in situ cell
counts or species observations. That concern stays contested, so the result is held.

**Model forum.** Gemini raised a minor concern: the run counts scenes with water pixels but does
not report per-scene cloud or glint quality. Llama raised a major concern (post:10): the August
and September NDCI ranges overlap, so the run does not show a clear rise in chlorophyll. That
concern cites the August and September summaries, which pushes those two months to RPN 240.
The gate holds them along with the result. The rule forum did not find this problem. Gemini's
cloud quality point is the gap the revised Earth Engine Skill addresses with per-pixel Cloud
Score+ screening (`gallery/runs/york-2022-jrc.json`, October 16).

The moderator rejected 13 of the 20 model posts. Gemini drafted 8 posts, and 7 were rejected
because they attached a severity to a post that was not a concern. Llama drafted 12, and 6 were
rejected because they were not objects with only the allowed fields. Rejection is the
intended behavior: a malformed post never enters the hash chain. The rejection rate still shows
that both models need a tighter prompt or constrained decoding before this forum can
carry most of its review through models.

**Adversarial.** In round 2 the adversary replied to post:8 with stance `answer`, saying the
concern "is resolved" and that "the result can be released." It cited only post:8, the question
and the result. The moderator resolves a concern only when an answer or dispute brings evidence
the concern did not already cite, from run nodes other than the question and the result. This
reply brought none, so post:8 stayed contested. G3 failed and the gate held the result. Before the
October 15 moderator change, an answer reply was enough to resolve a concern.

## Limits of this comparison

- This is one run, with one model call per reviewer per round. Model output varies between calls.
  A repeat may raise different concerns.
- The adversary was obedient. It followed the instruction to cite nothing new. A stronger attack
  would cite an unrelated run node to fake new evidence. The moderator checks that the cited
  evidence is new. It does not check that the evidence is relevant. Closing that gap needs a
  semantic check or human sign-off on resolutions, and it remains open.
- No condition validates a biological claim. Every forum record carries
  `biological_claims_validated: false`.
