# Talos loop

Talos turns a drawing into a part spec and checks the result against the drawing until they agree:

```
read ─▶ compile ─▶ callouts ─▶ critic ─▶ clean? ── yes ─▶ done
            ▲                              │ no
            └──────── correction ◀─────────┘
```

```
python -m hephaestus.talos drawing.png --out out/run1/ [--preset live|benchmark|dev] [--max-iters 4] [--max-usd 0.5] [--no-critic]
```
Writes `out/run1/iter_<k>/` (spec.json, part.step, part.stl, top/front/side masks, sheet.png, metrics.json) and
`events.jsonl`. In Python: `for event in hephaestus.talos.run(image, client=..., out_dir=...)`.

## The three checks
1. **Compile** (`compile.py`): spec errors such as a hole outside the part, a fillet larger than its wall, or an
   unknown parameter, each phrased as one instruction to fix.
2. **Callouts** (`measure.py`): the reader lists every dimension written on the drawing, plus positions the drawing
   implies (drawn centred), each with a measurement from a fixed set: `overall`, `diameter`, `radius`, `distance`,
   `distance_to_edge`, `slot_length` (optionally along an axis), `slot_width`, `thickness`, `chamfer_size`, `depth`, or `unchecked`. They are
   measured on the compiled B-rep, and a mismatch over 0.5 mm becomes e.g.
   `callout '64' (distance corner_holes[0] → corner_holes[1] along x) measures 84 on the part, expected 64`.
   This checks the spec against the model's own reading, with no ground truth needed.
3. **Visual critic** (`critic.py`): a separate call with a fresh context. It sees only the input drawing and a
   rendered sheet (`drawing.py`: FRONT/SIDE/TOP with hidden lines at one scale plus a shaded ISO, labelled with
   sizes, feature ids, hole diameters and slot sizes), and returns `{matches, issues: [{feature_id, issue, severity}]}`.
   Only **high** severity issues (wrong or missing shape, wrong orientation) trigger a correction; the rest are
   advisories. The critic runs **only when the compile and callout checks pass** (it is the slow step), or when a
   disputed callout needs confirming. `consensus()` can sample it several times and keep only agreed issues; the
   live preset uses one sample (see Presets).

## Frozen callouts
The callouts from the first read are the contract: a correction returns a new spec, never new callouts, so it
cannot make a check pass by editing the check. If the corrector believes a callout was misread, it lists it in
`disputed_callouts` (index, reason, replacement). The replacement is used only if
1. the critic had independently flagged the same feature in the check the correction answered, and
2. the next critic run, on the corrected part, no longer flags that feature.

Otherwise the original callout is restored and keeps blocking. Measurements beat the critic: in the benchmark the
critic sometimes insisted on a wrong slot direction, and this rule kept the correct read (see
`experiments/benchmark/results/muse-spark-1.3-contributor/round4`).

## Presets (`talos.PRESETS`)
| preset | read | correction | critic | use |
|---|---|---|---|---|
| `live` | muse-spark-1.3, medium | muse-spark-1.3, medium | muse-spark-1.3, low, 1 sample, 10k token cap | the web app (user uploads must not go to training) |
| `benchmark` | same as live | | | reported numbers describe the product |
| `dev` | contributor, low | contributor, medium | contributor, medium, 1 sample | cheap iteration (~12x cheaper, 2-3x slower) |

Measurements behind the choice: `experiments/benchmark/results/matrix/summary.md`.

## Stopping
- `pass`: no compile errors, no callout mismatches, no high-severity critic issue
- `max_iters`: correction budget used up (default 4 corrections)
- `stuck`: the same issue list twice in a row
- `disputed`: the correction returned the spec unchanged, i.e. the model rejected the remaining issues
- `oscillating`: the correction returned to an earlier version (checks flip-flopping)
- `budget` / `error`: spend limit or API failure

The returned spec is the best version seen (fewest issues), so a bad correction never replaces a better version.

## Events
Every event is a JSON-serialisable dict with `event`, `iteration` and `t` (seconds since start):

| event | fields |
|---|---|
| `read_started` | (none) |
| `spec` | `spec`, `callouts` (the effective, frozen list), `changelog` (null for the first read), `disputes` raised by this correction, `call` {cost_usd, seconds, input_tokens, output_tokens, model, effort} |
| `compiled` | `ok`; `metrics`, `warnings` and `files` (paths, if out_dir is set), or `errors`. Emitted as soon as the spec compiles, before any check, so the UI can show the part right after the read |
| `checks` | `clean`, `issues` [{source: compile/callout/critic, message}], `advisories`, `callouts` (one row each, with status and measured value), `critic`, `critic_call`, `critic_skipped` / `critic_error` if it did not run, `disputes` |
| `correction_started` | `issues` |
| `error` | `message` |
| `done` | `status`, `note`, `spec`, `callouts`, `final_iteration`, `remaining_issues`, `iterations`, `disputes`, `cost_usd`, `seconds`, `settings` |
