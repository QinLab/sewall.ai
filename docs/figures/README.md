# Sewall.ai figures

Diagrams of the prototype as it exists in October 2026. Each figure comes as SVG (vector, text drawn as paths) and PNG (200 dpi). Colors use the Okabe-Ito colorblind-safe palette.

| File | Caption |
|---|---|
| `fig1-architecture` | Every planner (MCP host model, provider model or scripted plan) proposes actions to one deterministic controller. The controller checks them against the Skill registry, access policies, budgets and integrity gates, calls bounded public Skills, and writes a hash-chained manifest, an HTML report and an offline replay. The Earth Engine Skills ran live on October 14, 2026 over the York and Lafayette Rivers for 2022; both runs pass offline replay. The `flood_sensors` Skill read live NOAA and USGS water-level sensors over six Chesapeake areas on October 15, 2026 (fig7). A scripted plan can be written by hand or by the `sewall consult` lab manager. Custodian federation is not implemented. |
| `fig2-run-lifecycle` | One run step by step: the propose, check, gate, execute, record and gate loop; the finish path through the pre-review gate, the separate review and the manifest; where each of the six final statuses arises. |
| `fig3-safe-stop` | The bundled safe-stop demonstration (`configs/scripted-safe-stop-demo.json`). A critical `missing_provenance` critique after action 2 quarantines the affected nodes and their descendants. No further action is dispatched, the review is withheld, and the report reads STOPPED, NOT SUCCESSFUL. |
| `fig4-offline-replay` | `sewall agent-replay` re-derives the saved results from the recorded events with nine groups of checks. It repeats no model call and no source request. |
| `fig5-bloom-mvp` | The Chesapeake bloom MVP. Steps 1 to 4 run on live NCBI in the recorded scripted run. The Sentinel-2 NDCI and AlphaEarth steps ran live on Earth Engine on October 14, 2026 in separate scripted runs (`configs/scripted-lafayette-2022-satellite.json` and the York River script; see fig6). Window alignment is planned and not implemented. |
| `fig6-ndci-2022` | Mean Sentinel-2 NDCI for each 2022 day in two live Earth Engine runs on October 16, 2026: the lower York River (22 days, manifest digest 4259e3f4, `gallery/runs/york-2022-jrc.json`) and the Lafayette River (19 days, digest b6f8db19, `gallery/runs/lafayette-2022-jrc.json`). Each day is one mosaic of the tiles under the cloud limit, screened per pixel with Cloud Score+ (cs_cdf at least 0.6) and masked to JRC Global Surface Water occurrence of at least 90%; days with fewer than 100 such pixels get no value. The October 14 runs used per-scene SCL water masks and gave the same day counts and the same broad pattern. The shaded band is the August to September window of the York River *M. polykrikoides* bloom simulated by Yu et al. (2025). Both runs pass offline replay. NDCI is a chlorophyll proxy; it does not identify a species or confirm a bloom. PNG only, drawn by `scripts/plot_ndci.py` from the run manifests. |
| `fig7-flood-sensors` | One live `flood_sensors` run over six Chesapeake areas, Norfolk to Baltimore, retrieved at 12:03 UTC on October 15, 2026 (`configs/scripted-flood-sensors-chesapeake.json`, 24 source requests, manifest digest 16b27ebc). Left: NWS gauges by observed flood category; none was at action stage or above. Right: each NOAA CO-OPS tide station's latest preliminary level minus its own minor flood threshold, in feet; all six were 1.4 to 3.4 feet below. USGS series carry no thresholds and are not plotted. The run passes offline replay. Readings are provisional. PNG only, drawn by `scripts/plot_flood.py` from the run manifest. |

## Regenerate

The script needs only matplotlib (tested with 3.10). It writes its outputs next to itself:

```
python3 docs/figures/make_figures.py
```

On a shared cluster, submit it as a short batch job; do not run it on a login node.

Edit `make_figures.py` when the prototype changes. A figure must not show a feature as working before it is implemented and tested against live sources.
