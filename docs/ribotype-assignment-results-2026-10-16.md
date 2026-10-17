# Ribotype assignment of public *M. polykrikoides* LSU records (2026-10-16)

This memo reports the first live runs of two local Skills that the growth-model expert now
proposes. `genbank.fetch-markers` retrieves at most 100 public GenBank sequences of one marker for
one taxon, with accession, isolate, country and date. `genotype.assign-ribotype` assigns each
sequence to a curated panel ribotype by deterministic k-mer nearest-reference scoring, or leaves it
unassigned with a reason. Context and the earlier evidence-discovery runs are in
[growth-consult-results-2026-10-15.md](growth-consult-results-2026-10-15.md).

A ribotype call is a label from published LSU groups. It is not a strain identity, a phylogenetic
placement or a growth phenotype. No growth parameter was estimated.

## Runs

Both questions were planned again by the lab manager and run on October 16 with the plan's
suggested limits and `--max-records 30 --per-search 2 --max-seconds 600`.

| Run | Steps | Records | Events | Content digest | Replay |
|---|---|---|---|---|---|
| mpoly-growth | 10 | 12 | 238 | `03f4cb7b` | valid |
| community | 12 | 12 | 273 | `3a8072e5` | valid |

In both plans the growth expert placed `fetch_markers` (LSU) after `taxon_inventory` and before
`assign_ribotype` for *M. polykrikoides*. In the community plan, *A. monilatum* has no panel, so
the expert kept `assess_source ncbi_genotype` and recorded the gap. Both runs fetched the same 93
LSU records and produced identical calls. Replay recomputed every assignment from the stored
sequences and panel and found no difference. Plans, manifests and replay results are in
`output/ribotype-consult/`, which git ignores. These runs use panel 0.1.1. The first runs, on
panel 0.1.0 (digests `0696c620` and `49b33016`), are in `output/ribotype-consult/panel-0.1.0/`
and gave identical calls for all 93 records.

## Panel

`sewall/panels/margalefidinium-polykrikoides-lsu.json`, version 0.1.1, digest `96c78cec`.

- 14 references across 4 ribotypes: East Asian (4), American/Malaysian (3) and Philippine (1),
  from Iwataki et al. 2008; Mediterranean (6), from Rene et al. 2013.
- Labels follow each isolate's origin as given in its source publication. Every label needs a
  full-text check.
- Settings: k = 10, min_score 0.9, min_margin 0.05, min_cover 0.2.
- Excluded: AB295047.1 and KC577589.1, because their profiles did not match their origin labels.
- A panel reference is scored against the other references, never against itself.

## Results

75 of 93 records were assigned and 18 were left unassigned.

| Call | Records | Countries in the records |
|---|---|---|
| East Asian | 43 | South Korea, Japan, China; 22 without a country qualifier |
| American/Malaysian | 21 | USA (incl. Virginia, PQ384573.1), Puerto Rico, Mexico, Malaysia, Indonesia (Lampung), Iran, Oman, UAE |
| Mediterranean | 9 | Spain; KU160136.1 to KU160138.1 have no country qualifier (margins near 0.08) |
| Philippine | 2 | Both are the excluded references (see below) |
| Unassigned, below_min_score | 8 | China (PZ671379.1 to PZ671384.1, best score 0.77 to 0.79); JX967271.1 (USA) and JQ616826.1 (Mexico), both near 0.66 |
| Unassigned, panel_region_gap | 10 | EF5066xx series (Japan, South Korea, USA, Mexico), EF110557.1, JQ616831.1, AB295046.1 |

## Items for review

1. **Both excluded references are called Philippine.** AB295047.1 (Japan, Omura Bay) scores 0.96
   against the Philippine reference AB295046.1, with margin 0.22. KC577589.1 (Spain, Ametlla
   Harbour) scores 0.91, with margin 0.35. Neither publication's origin implies Philippine. The labels
   should be checked in Iwataki et al. 2008 and Rene et al. 2013 before either record is used.
2. **The exclusion note for KC577589.1 was corrected in panel 0.1.1.** Version 0.1.0 said it
   matched East Asian references at 0.96. Its best scores are Philippine 0.91 (AB295046.1),
   American/Malaysian 0.56, East Asian 0.49 and Mediterranean 0.48. The note for AB295047.1 was
   checked and is correct: Philippine 0.96, East Asian 0.64. Only the note text and version
   changed, so the panel digest changed and every call stayed the same.
3. **Partial reference coverage causes four strong matches to be withheld.** JQ616831.1,
   EF110557.1, EF506626.1 and EF506628.1 score 0.99 to 1.0 against American/Malaysian references,
   with margins above 0.58. They stay unassigned because no Philippine reference, and for
   JQ616831.1 no East Asian or Mediterranean reference either, covers their region. A second Philippine reference, or a gap rule limited to the top two ribotypes,
   would change these calls. Either choice should be made on the labels, not tuned to the counts.
4. **The six Hainan records (PZ671379.1 to PZ671384.1) do not match any panel group closely.**
   They may be a novel lineage or a different LSU region. They are results, not errors.
5. **Thresholds were set on these same 93 records,** so the counts above are not an independent
   test of the panel.

## Linking growth-studied strains to ribotype calls

A third local Skill, `genotype.link-strains`, pairs a curated table of growth-studied strains with
the ribotype calls. The table is `sewall/panels/margalefidinium-polykrikoides-growth-strains.json`,
version 0.1.0. It lists six named strains whose growth was measured in published experiments,
with origin, tested range, results, citation, and whether the entry was read from the abstract
or the full text. A strain's label is recorded only where its paper states one. The Skill matches
each strain name to GenBank strain or isolate qualifiers after removing case and punctuation. It
then reports the calls for the matched records. A shared ribotype does not imply a shared growth
response, and the Skill estimates no parameter.

Both questions were planned again and run on October 16. The growth expert placed `link_strains`
directly after `assign_ribotype`.

| Run | Steps | Events | Content digest | Replay |
|---|---|---|---|---|
| mpoly-growth | 11 | 254 | `eecfedc1` | valid |
| community | 13 | 289 | `13f4a4e0` | valid |

Outputs are in `output/strains-consult/`. Both runs gave the same linkage.

| Strain | Origin | Responses measured | GenBank record | Call | Label check |
|---|---|---|---|---|---|
| MP-LRCB-082419 | Lafayette River, Virginia, 2019 | temperature, salinity | PQ384573.1 | American/Malaysian (0.99) | different label system: paper says Group III |
| CP-1 | Flanders Bay, New York, 2006 | temperature | MK685214.1 ("CP1") | American/Malaysian (1.0) | different label system: paper says Malaysian-American |
| CP2013, CP2018 | Korea | temperature (heatwave) | none | none | no published label |
| IS ND69 | Isahaya Bay, Japan, 2003 | temperature by salinity | none | none | no published label |
| CPYG0106 | Yatsushiro Sea, Japan, 2001 | nitrogen, phosphorus kinetics | none | none | no published label |

### Items for review

1. **The Virginia strain's call agrees with its paper's label once the label is translated.**
   Perez-Vega et al. 2026 (J Phycol, doi 10.1111/jpy.70201) equate Group III with the
   American/Malaysian ribotype. The Skill does not apply that mapping, so it reports a different
   label system. That is the expected result, not a conflict.
2. **The CP-1 match is by name only.** MK685214.1 carries strain "CP1" from a phylogeny paper. The
   record has not been confirmed as the Griffith and Gobler 2016 culture. Confirm the link with the
   record's authors or the paper's methods before using CP-1's temperature response for the
   American/Malaysian group.
3. **Only American/Malaysian has growth-studied strains with sequences.** The four Asian strains
   have no LSU record under their names. East Asian, Mediterranean and Philippine have no paired
   growth data. Sequencing these cultures, or finding their records under other names, is the
   most direct way to fill the gap.
4. **Responses are not comparable across studies.** Media, irradiance, photoperiod and ranges
   differ. Entries for MP-LRCB-082419, CP2013 and CP2018 were read from abstracts only.
