# Hephaestus

**Turn a sketch, a prompt or a real object into a parametric CAD part.**
Powered by **Talos**, an agent that generates a part, checks it against the input and corrects itself until the geometry matches.

Built during the [Meta Global AI Developer Hackathon](https://dev.meta.ai/events/global-hackathon), Oct 26 – Nov 4, 2026, on Meta's Muse Spark and SAM 3.1 models.

## Web app

**Live: <https://hephaestus-talos.netlify.app>** (backend: Modal).

`web/` (static, Netlify) + `server/` (FastAPI on Modal). Upload a sketch or photo, take one with your phone, or
describe the part in words; watch Talos read, build, check and correct it live; drag the parameter sliders to
rebuild the part instantly; download STEP, STL and the spec. Six examples replay recorded runs instantly, two of
them showing Talos catching and fixing an error. Deployment and limits: [`server/README.md`](server/README.md).

## How it works

1. **Read.** Muse Spark turns the drawing into a structured part spec (features + named parameters, see
   [`docs/spec-v1.md`](docs/spec-v1.md)) and lists every dimension it read, as *callouts*.
2. **Compile.** A deterministic compiler builds the part with CadQuery: STEP, STL, metrics, silhouettes.
3. **Verify.** Callouts are measured on the compiled geometry. A separate critic call compares the drawing with a
   rendered drawing sheet of the part.
4. **Correct.** Any compile error, callout mismatch or critic issue goes back to the model, until everything passes.

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

**Injected errors** ([`experiments/loop-eval`](experiments/loop-eval/), live settings): the loop starts from a
correct spec with one deliberate mistake and the real drawing, 2 runs each.

| error | caught by | fixed | mean time per run |
|---|---|---|---|
| slot turned 90° | callouts | 2/2 | 23 s |
| hole moved 5 mm | callouts | 2/2 | 10 s |
| hole missing | callouts | 2/2 | 21 s |
| wrong diameter | callouts | 2/2 | 18 s |
| chamfer instead of fillet | callouts | 2/2 | 15 s |
| plate X/Y swapped | callouts | 2/2 | 12 s |
| cable hole drilled through both walls | critic | 2/2 | 33 s |
| upright at the front instead of the back | **not caught** | 0/2 | |

False alarms on the 8 correct parts: 1/16 checks with a single critic sample, 0/4 on a recheck of the affected
parts with the 2-sample critic.

## Experiments

| Experiment | Question |
|---|---|
| [`reading-spike`](experiments/reading-spike/) | Can Muse Spark read a dimensioned sketch into a correct part spec? (v0 box spec) |
| [`reading-spike-v2`](experiments/reading-spike-v2/) | Can it fill in the full parametric spec v1? 26/27 perfect |
| [`loop-eval`](experiments/loop-eval/) | Does the loop catch and fix known errors, without false alarms? |
| [`benchmark`](experiments/benchmark/) | Reading only vs the full loop on 8 parts × 3 input qualities |
