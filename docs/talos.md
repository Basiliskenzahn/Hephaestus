# Talos loop

Talos turns a drawing into a part spec and checks the result against the drawing until they agree:

```
read ─▶ compile ─▶ callouts ─▶ critic ─▶ clean? ── yes ─▶ done
            ▲                              │ no
            └──────── correction ◀─────────┘
```

```
python -m hephaestus.talos drawing.png --out out/run1/ [--max-iters 4] [--max-usd 0.5] [--no-critic]
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
   The critic is sampled twice in parallel; an issue triggers a correction only if both samples report it as **high**
   severity (wrong or missing shape, wrong orientation) on the same feature. Everything else is reported as an
   advisory. In the evaluations, real errors were reported consistently, while misreadings of photos (a slot's
   direction, positions) flipped from one sample to the next, and exact sizes and positions are the callouts' job.

## Stopping
- `pass`: no compile errors, no callout mismatches, no high-severity critic issues
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
| `spec` | `spec`, `callouts`, `changelog` (null for the first read), `call` {cost_usd, seconds, input_tokens, output_tokens} |
| `compiled` | `ok`; `metrics`, `warnings` and `files` (paths, if out_dir is set), or `errors` |
| `checks` | `clean`, `issues` [{source: compile/callout/critic, message}], `advisories`, `callouts` (one row each, with status and measured value), `critic`, `critic_call` |
| `correction_started` | `issues` |
| `error` | `message` |
| `done` | `status`, `note`, `spec`, `callouts`, `final_iteration`, `remaining_issues`, `iterations`, `cost_usd`, `seconds` |
