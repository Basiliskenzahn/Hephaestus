# Hephaestus

**Turn a sketch, a prompt or a real object into a parametric CAD part.**
Powered by **Talos**, an agent that generates a part, checks it against the input and corrects itself until the geometry matches.

Built during the [Meta Global AI Developer Hackathon](https://dev.meta.ai/events/global-hackathon), Oct 26 – Nov 4, 2026, on Meta's Muse Spark and SAM 3.1 models.

## How it works

1. **Read.** Muse Spark turns the drawing into a structured part spec (features + named parameters, see
   [`docs/spec-v1.md`](docs/spec-v1.md)) and lists every dimension it read, as *callouts*.
2. **Compile.** A deterministic compiler builds the part with CadQuery: STEP, STL, metrics, silhouettes.
3. **Verify.** Callouts are measured on the compiled geometry. A separate critic call compares the drawing with a
   rendered drawing sheet of the part.
4. **Correct.** Any compile error, callout mismatch or critic issue goes back to the model, until everything passes.

Details: [`docs/talos.md`](docs/talos.md). Run it with `python -m hephaestus.talos drawing.png --out out/run1/`.

## Results (muse-spark-1.3-contributor, Oct 2026)

**Benchmark**: 8 parts (plates, brackets, a revolved bushing, a shelled enclosure, an angled bracket, a pocket
plate, a shaft collar), each as a clean drawing, a fake phone photo and a rougher pencil photo, 2 runs each. A run
is *perfect* when the compiled part matches the true part (bbox, volume IoU ≥ 0.98, every written dimension within
0.5 mm). [`experiments/benchmark`](experiments/benchmark/), round 3:

| input | perfect, read only | perfect, Talos loop | cost per part, read / loop | median time, read / loop |
|---|---|---|---|---|
| clean drawing | 16/16 | 16/16 | $0.002 / $0.005 | 53 s / 138 s |
| phone photo | 13/16 | 15/16 | $0.002 / $0.005 | 57 s / 130 s |
| rough pencil photo | 13/16 | 12/16 | $0.002 / $0.006 | 59 s / 134 s |

**Injected errors** ([`experiments/loop-eval`](experiments/loop-eval/)): start the loop from a correct spec with
one deliberate mistake. All 6 error types were caught and fixed with one correction, in 12/12 runs. The checks
raised no false alarms on the 8 correct parts (0/16).

| error | caught by | fixed |
|---|---|---|
| slot turned 90° | callouts + critic | 2/2 |
| hole moved 5 mm | callouts | 2/2 |
| hole missing | callouts + critic | 2/2 |
| wrong diameter | callouts | 2/2 |
| chamfer instead of fillet | callouts + critic | 2/2 |
| plate X/Y swapped | callouts | 2/2 |

## Experiments

| Experiment | Question |
|---|---|
| [`reading-spike`](experiments/reading-spike/) | Can Muse Spark read a dimensioned sketch into a correct part spec? (v0 box spec) |
| [`reading-spike-v2`](experiments/reading-spike-v2/) | Can it fill in the full parametric spec v1? 26/27 perfect |
| [`loop-eval`](experiments/loop-eval/) | Does the loop catch and fix known errors, without false alarms? |
| [`benchmark`](experiments/benchmark/) | Reading only vs the full loop on 8 parts × 3 input qualities |
