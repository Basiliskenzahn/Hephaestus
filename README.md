# Hephaestus

**Turn a sketch, a prompt or a real object into a parametric CAD part.**
Powered by **Talos**, an agent that generates a part, checks it against the input and corrects itself until the geometry matches.

Built during the [Meta Global AI Developer Hackathon](https://dev.meta.ai/events/global-hackathon), Oct 26 – Nov 4, 2026, on Meta's Muse Spark, SAM 3.1 and Muse Voice Transcribe models.

## Web app

**Live: <https://hephaestus-talos.netlify.app>** (backend: Modal).

`web/` (static, Netlify) + `server/` (FastAPI on Modal). Upload a sketch or photo, take one with your phone, or
describe the part in words; watch Talos read, build, check and correct it live; drag the parameter sliders to
rebuild the part instantly; change it in words, typed or spoken ("make the holes M6", "remove the slot"); download
STEP, STL and the spec. Six examples replay recorded runs instantly. Five are
live-model runs; the enclosure, the one showing Talos catch and fix an error, was recorded with the cheaper
development model, and says so (the live model reads that photo right the first time). Deployment and limits: [`server/README.md`](server/README.md).

## How it works

1. **Read.** Muse Spark turns the drawing into a structured part spec (features + named parameters, see
   [`docs/spec-v1.md`](docs/spec-v1.md)) and lists every dimension it read, as *callouts*.
2. **Compile.** A deterministic compiler builds the part with CadQuery: STEP, STL, metrics, silhouettes.
3. **Verify.** Callouts are measured on the compiled geometry. **SAM 3.1** segments each view of the drawing (after
   finding and straightening the sheet in a photo) and the outline is compared with the part's silhouette: a part
   that is mirrored, rotated or has other proportions is caught without asking a model's opinion. Only if both pass,
   a separate critic call compares the drawing with a rendered drawing sheet of the part.
4. **Correct.** Any compile error, callout mismatch, shape issue or critic issue goes back to the model, until
   everything passes. The critic cannot loop on its own: an issue only the critic sees gets one correction, and if
   that does not hold up, the measured part is kept and the critic's issue is shown as an unverified note.
5. **Edit.** After that, the part can be changed in words: typed, or spoken and transcribed by **Muse Voice
   Transcribe** (the transcript is shown for correction first). Muse Spark (low effort) turns the instruction into a
   small structured edit (parameter values, holes, slots, patterns, fillets, chamfers, pockets, or a clarifying
   question when the instruction is ambiguous or impossible); our code applies it, rebuilds and validates. The part
   is then marked *edited*: the drawing's dimensions you changed leave the check, the others still count.

Details: [`docs/talos.md`](docs/talos.md). Run it with `python -m hephaestus.talos drawing.png --out out/run1/`.

## Results (Oct 2026)

Everything below is measured on compiled geometry: a part is *perfect* when it matches the true part (bbox,
volume IoU ≥ 0.98, every dimension written on the drawing within 0.5 mm).

**Speed** with the live settings (`muse-spark-1.3`, see [`docs/talos.md`](docs/talos.md#presets-talospresets)):
the first part appears after **25 s** (median; p90 36 s) and a full verified run takes **38 s** (median; p90 76 s),
about $0.05. With the cheaper contributor models the same run took 111–137 s.

**Benchmark**: 8 parts (plates, brackets, a revolved bushing, a shelled enclosure, an angled bracket, a pocket
plate, a shaft collar) as clean drawings, fake phone photos and rougher pencil photos
([`experiments/benchmark`](experiments/benchmark/)).

| input | live settings, 1 run per part: read only → with loop | contributor models, 2 runs per part: read only → with loop |
|---|---|---|
| clean drawing | 8/8 → 8/8 | 16/16 → 16/16 (earlier round) |
| phone photo | 8/8 → 7/8 ¹ | 15/16 → 15/16 |
| rough pencil photo | 7/8 → 7/8 | 14/16 → 14/16 |

The honest summary: **on these drawings the loop does not raise accuracy**. Reading alone is already right 88–100%
of the time, and the remaining misses are misreadings that every check shares: the angled bracket's two close
dimensions (15 and 10) get swapped, and the checks built from the same reading agree with it. The loop's value is
catching the errors reads *do* make sometimes (it fixed duplicate feature ids, and a cable hole that wrongly went
through the back wall, in earlier rounds) and not making correct reads worse.

¹ The loop made one correct read worse here: a single critic sample hallucinated a kink in the bracket's slope and
the correction invented an unwritten 28 mm height to add it. Since then the critic needs two samples to agree and
corrections may not invent dimensions; on a rerun the critic hallucinated the same kink in both samples, and the
correction refused because the drawing gives no such size, so the read stayed intact.

**Injected errors**: a correct spec with one deliberate mistake, checked against the real drawing. Which check
catches it ([`experiments/loop-eval`](experiments/loop-eval/), [`experiments/shape-check`](experiments/shape-check/),
critic flags from [`experiments/benchmark/results/matrix`](experiments/benchmark/results/matrix/summary.md)):

| error | callouts | shape check (SAM 3.1) | critic (2 samples) | fixed by the loop (live, 2 runs) |
|---|---|---|---|---|
| slot turned 90° | ✓ | – | ✓ | 2/2, 23 s |
| hole moved 5 mm | ✓ | – | – | 2/2, 10 s |
| hole missing | ✓ | – | ✓ | 2/2, 21 s |
| wrong diameter | ✓ | – | – | 2/2, 18 s |
| chamfer instead of fillet | ✓ | – | ✓ | 2/2, 15 s |
| plate X/Y swapped | ✓ | note only ² | – | 2/2, 12 s |
| cable hole drilled through both walls | – | – | ✓ | 2/2, 33 s |
| upright at the front instead of the back | – | **✓ (3/3 inputs)** | – | not re-run since the shape check ¹ |
| angled bracket's slope mirrored | – | **✓ (3/3 inputs)** | not tested | not run |
| hole pair moved to the other end | ✓ | – | not tested | not run |

"3/3 inputs" = the clean drawing, the phone photo and the rough pencil photo. The checks complement each other:
callouts are exact on every written dimension, the shape check sees the outline of each view (orientation and
proportions, which no single dimension captures), the critic sees what neither measures (which walls a hole goes
through). None of them sees a misreading they all share (the angled bracket's 15/10 swap above).

**No false alarms**: the 8 correct parts pass the callouts and the shape check on all 24 inputs (0/24 blocking
shape issues: for a correct part a mirrored or rotated outline never fits the drawing better than the part as built,
while the injected flips fit better by at least 0.29 IoU; the flag needs 0.10), and the 2-sample critic
raised 0/4 on a recheck (1/16 with a single sample, which is why it takes two). The shape check's hole comparison
is switched off: it flagged 18 of 24 correct parts (text, centre lines and gaps in sketched circles), so it would
only be noise.

**Hand sketches** ([`experiments/hand-sketches`](experiments/hand-sketches/)): the 8 parts drawn sloppily in
handwriting fonts, not to scale, with a crossed-out and corrected number, as scans and phone photos (16 images,
contributor models, 1 run each). Most misses were misread numbers, which every check shares. So every written number
is now **read a second time** from a crop of where the read says it is written, with nothing else in view (one extra
call, all crops in one request, in parallel with the first compile: ~10 s, ~$0.0003 per run with contributor models).
Numbers the two readings disagree on are shown on the drawing as chips with both readings; one tap sets the
parameter and rebuilds, no model call ([`experiments/numbers`](experiments/numbers/)).

| hand sketches (16) | read perfect | perfect after the user taps the flagged numbers |
|---|---|---|
| before (read only, loop on) | 11/16 | – |
| with the number rules + re-read | 13/16 | **14/16** |

The misread 60 (written 40) on the block was flagged with "40" as the second reading; tapping it makes the part
perfect. A crossed-out 10 is no longer used (the read prompt now says so; the plate and pocket plate were also redrawn so the strike
is visible). False flags: 15 correct numbers on 16 sketches (max 4 on one, ~1 per sketch), 16 on the 24 printed
benchmark images (max 2). The remaining misses are not digit errors (a hole through the wrong wall, the 15/10 swap of
two correct numbers), which a re-read cannot see. Single runs on partly redrawn images: read the before/after as
indicative, not exact.

¹ In the earlier loop evaluation, before the shape check existed, nothing caught it (0/2).
² The shape check's proportion test flags it on all 3 inputs, but only as a note: hand sketches are not to scale
(a correct part's view came out 1.47× off in the hand-sketch set), so proportions cannot block.

## Experiments

| Experiment | Question |
|---|---|
| [`reading-spike`](experiments/reading-spike/) | Can Muse Spark read a dimensioned sketch into a correct part spec? (v0 box spec) |
| [`reading-spike-v2`](experiments/reading-spike-v2/) | Can it fill in the full parametric spec v1? 26/27 perfect |
| [`loop-eval`](experiments/loop-eval/) | Does the loop catch and fix known errors, without false alarms? |
| [`benchmark`](experiments/benchmark/) | Reading only vs the full loop on 8 parts × 3 input qualities |
| [`sam-spike`](experiments/sam-spike/) | Can SAM 3.1 segment line drawings (views, through-holes) and find the paper in a photo? |
| [`shape-check`](experiments/shape-check/) | Does the SAM shape check flag wrong parts without false alarms on correct ones? |
| [`edits`](experiments/edits/) | Voice and typed edits: 8 spoken instructions → Muse Voice Transcribe → structured edit (recorded for the tests) |
| [`numbers`](experiments/numbers/) | Does an independent re-read of every written number flag misread handwriting, without flagging correct numbers? |
| [`load`](experiments/load/) | Does SAM mask decoding stall the sliders? `/compile` p50 during 3 runs: 730 ms → 134 ms with worker processes |

## Third-party code

- **[meta-sam-parser](https://pypi.org/project/meta-sam-parser/) 0.1.0** (pip dependency, not vendored) decodes SAM
  3.1's mask payloads in `hephaestus/sam.py`. Published by Meta Platforms, Inc. (source:
  <https://github.com/meta-models/meta-sam>) under the **SAM License** (Nov 19, 2025), which grants a
  non-exclusive, worldwide, royalty-free licence to use, reproduce, distribute and modify the SAM Materials; anyone
  redistributing them must include the licence. This repo only depends on the package; the licence text ships in
  the package's `licenses/` folder.
- CadQuery / OpenCascade (Apache 2.0 / LGPL), FastAPI, three.js, NumPy, SciPy, OpenCV, Pillow: standard open-source
  dependencies installed from their package registries.
