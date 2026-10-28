# Talos loop

Talos turns a drawing into a part spec and checks the result against the drawing until they agree:

```
read ─▶ compile ─▶ callouts + shape check ─▶ critic ─▶ clean? ── yes ─▶ done
            ▲                                            │ no
            └──────────────── correction ◀───────────────┘
SAM 3.1: sheet (photo) ─▶ view outlines   (in parallel with the read and the first compile)
```

```
python -m hephaestus.talos drawing.png --out out/run1/ [--preset live|benchmark|dev] [--max-iters 4] [--max-usd 0.5] [--no-critic]
```
Writes `out/run1/iter_<k>/` (spec.json, part.step, part.stl, top/front/side masks, sheet.png, metrics.json) and
`events.jsonl`. In Python: `for event in hephaestus.talos.run(image, client=..., out_dir=...)`.

## The checks
1. **Compile** (`compile.py`): spec errors such as a hole outside the part, a fillet larger than its wall, or an
   unknown parameter, each phrased as one instruction to fix.
2. **Callouts** (`measure.py`): the reader lists every dimension written on the drawing, plus positions the drawing
   implies (drawn centred), each with a measurement from a fixed set: `overall`, `diameter`, `radius`, `distance`,
   `distance_to_edge`, `slot_length` (optionally along an axis), `slot_width`, `thickness`, `chamfer_size`, `depth`, or `unchecked`. They are
   measured on the compiled B-rep, and a mismatch over 0.5 mm becomes e.g.
   `callout '64' (distance corner_holes[0] → corner_holes[1] along x) measures 84 on the part, expected 64`.
   This checks the spec against the model's own reading, with no ground truth needed.
3. **Shape check** (`shape_check.py`, SAM 3.1): see below. Blocks like the callouts.
4. **Visual critic** (`critic.py`): a separate call with a fresh context. It sees only the input drawing and a
   rendered sheet (`drawing.py`: FRONT/SIDE/TOP with hidden lines at one scale plus a shaded ISO, labelled with
   sizes, feature ids, hole diameters and slot sizes), and returns `{matches, issues: [{feature_id, issue, severity}]}`.
   Only **high** severity issues (wrong or missing shape, wrong orientation) trigger a correction; the rest are
   advisories. The critic runs **only when the compile, callout and shape checks pass** (it is the slow step), or
   when a disputed callout needs confirming. It is sampled twice in parallel and only issues both samples raise on the
   same feature block (see Presets).

## Number re-read (`reread.py`)
The checks above all start from the read, so a misread digit (a handwritten 40 read as 60) passes all of them. The
read gives every callout a `text_box` (where its number is written, 0-1000 normalised), a `confidence` and the
`parameter` that holds it. Right after the read, in parallel with the first compile, each box is cropped from the
full-resolution image (generous margin: grounding boxes are often a few % off; upscaled to >= 96 px) and all crops
go in one request with a minimal prompt ("transcribe the number nearest the centre", no drawing, no other numbers),
read model, low effort. Same number -> `confirmed`; a different number, or "unclear" on a callout the read was not
sure of -> `uncertain` with both readings; "unclear" on a confident callout -> `unverified` (shown, not highlighted).
Uncertain numbers never trigger a correction: the loop continues with the first reading and the UI shows chips on
the drawing; choosing a value sets the parameter and recompiles (no model call). A failed re-read never fails the run.

## Shape check (SAM 3.1)
Pixels of the drawing against pixels of the part, with no model opinion involved. Per run, once:
1. **Sheet** (photos only; a scan with a white border is used as is): SAM 3.1 with the prompt "sheet of paper" →
   largest mask → 4 corners → homography → the straightened sheet. Fallback: a brightness threshold. Starts with
   the read.
2. **View outlines**: the read also returns `views` (top/front/side, a box 0–1000 per view). Each box is mapped onto
   the straightened sheet and widened to the bbox of the closed ink loops near it (model boxes are often shifted
   5–15 %). SAM gets one request for all views: box + one positive point, the deepest pixel of the largest region
   enclosed by ink (with a box alone SAM segments the pencil strokes, not the face). The mask is cut back to what
   the drawing's lines enclose (SAM leaks into labels and dimension areas), thin leaks are opened away, holes filled.
   An outline spanning less than half its box is rejected.

Per iteration, every usable view against the part's silhouette (both cropped to their own bbox):
- **orientation**: IoU of the outlines as is, mirrored left-right, mirrored up-down and rotated 180°, on a 96×96
  grid. Flagged only when a transformed version beats as-is by `ORIENT_MARGIN` = 0.10: a relative decision, never an
  absolute IoU threshold. Eval: correct parts ≤ 0.000, injected flips ≥ 0.289. Message, e.g. `side view: the
  drawing has its tallest section at the back (right), the part has it at the front (left); the part's outline is
  the drawing's mirrored along Y. Fix the positions along Y.`
- **aspect ratio** (advisory): noted when width:height differs by more than `ASPECT_TOL` = 45 %. It used to block,
  but hand sketches are not to scale: in `experiments/hand-sketches` a correct block's top view came out 1.47x off
  and raised a false alarm. Swapped sizes are caught by the callouts anyway.
- **holes** (off by default, `HOLE_CHECK`; the eval switches it on): closed regions in the drawing (text loops, arrowheads, sharp-cornered hidden-line strips
  and regions below 0.2 % of the outline dropped; regions split by centre lines merged; boundary must be solid ink)
  against the closed regions of the part's own line drawing through the same detector, by count and rough position
  (3×3 grid). Not blocking: it flags 18/24 correct parts, mostly because the SAM outline is larger than the part and
  sketchy circles have gaps.

If there are no view boxes, SAM fails or no outline is usable, `shape_check` says so (`available: false`, `note`)
and the run continues with the other checks. Cost: $0.0025 per SAM request (2 per photo run, 1 per scan). Time: the
sheet takes 4–5 s (mostly decoding the page-sized mask in pure Python) while the read runs; the views take ~1.4 s
locally plus a ~1.3 s SAM round trip after the read, in parallel with the compile; the comparison takes 0.1–0.8 s per
iteration. In practice the first check waits 1–2 s longer. Eval:
`experiments/shape-check/` (`python eval.py --offline` re-scores from the caches).

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
| `live` | muse-spark-1.3, medium | muse-spark-1.3, medium | muse-spark-1.3, low, 2 samples (consensus), 10k token cap | the web app (user uploads must not go to training) |
| `benchmark` | same as live | | | reported numbers describe the product |
| `dev` | contributor, low | contributor, medium | contributor, medium, 1 sample | cheap iteration (~12x cheaper, 2-3x slower) |

Measurements behind the choice: `experiments/benchmark/results/matrix/summary.md`. Read effort low was re-tested with the full
loop on the 8 rough photos (Oct 28): 5/8 perfect against 7/8 at medium, 18 s vs 22 s median read, so medium stays
(`experiments/benchmark/results/muse-spark-1.3/live-low-rough/compare.md`). The `views` field of the read does not
slow it down (`experiments/read-views/`: same time and tokens with and without it, dev preset).

## Stopping
- `pass`: no compile errors, no callout mismatches, no shape issue, no high-severity critic issue
- `rejected`: the read says the input cannot become a part (not a drawing, unreadable, no dimension at all,
  contradictory or impossible); `note` is the message for the user. We never guess the scale: a sketch or text
  without a single size is rejected with a request for one
- `unverified`: the critic cannot loop on its own. A check whose only issues come from the critic gets **one**
  correction per flagged feature. If that correction declines (spec unchanged), breaks a measured check (compile,
  callout, shape; the version before it is kept), or the critic flags the same feature again, the run stops: the
  measured part is returned and the critic's issue is reported in `unverified_notes`. No second critic pair runs
  after a declined correction. (Production, Oct 27: the critic pair invented a kink in the angled bracket's slope;
  the old loop spent two corrections and a callout regression on it, $0.247 and 224 s.)
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
| `rejected` | `input_kind` (no_dimensions, not_a_drawing, unreadable, or description/drawing with a problem), `message` (shown to the user), `call`. The read decides this in the same call (fields `input_kind`, `problem`); nothing is compiled |
| `spec` | `spec`, `callouts` (the effective, frozen list), `changelog` (null for the first read), `disputes` raised by this correction, `call` {cost_usd, seconds, input_tokens, output_tokens, model, effort} |
| `compiled` | `ok`; `metrics`, `warnings` and `files` (paths, if out_dir is set), or `errors`. Emitted as soon as the spec compiles, before any check, so the UI can show the part right after the read |
| `shape_check` | `available`, `note` (why not), `views` [{view, status ok/issue/advisory/unavailable, orientation (IoU per transform), orientation_gap, aspect {drawing, part}, holes {drawing, part}, issues, advisories, files {input, compare}}], `issues` (blocking, source `shape`), `advisories` (holes), `sheet` {method sam/threshold/scan/none, rectified, quad}, `files` {sheet_found, sheet_rectified, input_<view>}, `sam` {calls, cost_usd, seconds}. Emitted before `checks` in every iteration of a drawing run |
| `numbers` | `available`, `note` (why not), `numbers` [{index, label, value, parameter, text_box, confidence, status confirmed/uncertain/unverified/not_located, second (the re-read value), reread (its text)}], `uncertain` (count), `calls`, `cost_usd`, `seconds`. Once per drawing run, after `compiled` if the re-read is done, else after the first `checks` |
| `checks` | `clean`, `issues` [{source: compile/callout/shape/critic, message}], `advisories`, `shape_advisories`, `callouts` (one row each, with status and measured value), `critic`, `critic_call`, `critic_skipped` / `critic_error` if it did not run, `disputes` |
| `correction_started` | `issues` |
| `error` | `message` |
| `done` | `status`, `note`, `spec`, `callouts`, `final_iteration`, `remaining_issues`, `unverified_notes` (critic issues left as notes, status `unverified`), `iterations`, `disputes`, `cost_usd`, `seconds`, `settings` |
