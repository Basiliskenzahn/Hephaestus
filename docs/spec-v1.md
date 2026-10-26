# Part spec v1

A part is a JSON document: named parameters plus an ordered list of features. The compiler
(`hephaestus/compile.py`) turns it into a solid, STEP/STL files, metrics and silhouettes. The same
spec always compiles to byte-identical output.

```
python -m hephaestus.compile examples/plate.json --out out/ [--set width=100]
python -m hephaestus.spec            # print the JSON Schema (for structured output)
```

## Conventions
- Units are mm, angles are degrees.
- Origin = lower-left-front corner of the part's bounding box. X right, Y back, Z up. The compiler doesn't
  move geometry; if the bbox doesn't start at (0, 0, 0) you get a warning.
- **Sketch planes** use 2D coordinates (u, v), and w is the plane's normal axis:

  | plane | u | v | w (extrude direction "positive") |
  |---|---|---|---|
  | XY | x | y | +z |
  | XZ | x | z | +y |
  | YZ | y | z | +x |

- **Faces** (`top`, `bottom`, `front`, `back`, `left`, `right`) are the faces of the current part's
  *bounding box*. Holes and slots placed on a face use that face's plane: top/bottom → XY, front/back → XZ,
  left/right → YZ. Note that "top" of an L-bracket is the top of the upright (z = 40), not the base.

## Parameters and expressions
Every dimension is a number or a string expression: numbers, parameter names, `+ - * /`, parentheses
and unary minus. Parameters can reference each other; cycles and unknown names are errors.

```json
"parameters": {
  "width": {"value": 80, "description": "plate length along X", "min": 30},
  "inset": {"value": "width / 10", "description": "hole inset"}
}
```

## Profiles
Used by `extrude` and `revolve`. All coordinates are (u, v) in the sketch plane.

```json
{"shape": "rect", "u": 0, "v": 0, "width": 80, "height": 50, "anchor": "corner"}
{"shape": "rounded_rect", "u": 40, "v": 25, "width": 80, "height": 50, "radius": 5, "anchor": "center"}
{"shape": "circle", "u": 20, "v": 20, "diameter": 10}
{"shape": "polygon", "points": [[0, 0], [30, 0], [0, 20]]}
{"shape": "path", "start": [0, 0], "segments": [
  {"kind": "line", "to": [30, 0]},
  {"kind": "arc_center", "center": [30, 10], "to": [30, 20], "clockwise": false},
  {"kind": "arc3", "via": [15, 25], "to": [0, 20]}
]}
```
A path closes itself with a straight line if the last point isn't the start.

## Features

### extrude
```json
{"id": "base", "type": "extrude", "plane": "XY", "offset": 0,
 "profile": {"shape": "rect", "width": "width", "height": "depth"},
 "distance": "thickness", "direction": "positive", "operation": "add"}
```
`direction`: `positive` (along +w from the plane), `negative`, or `symmetric` (half each way).
`operation`: `add` or `cut`. The first feature must be an `add`.

### revolve
```json
{"id": "body", "type": "revolve", "plane": "XZ", "offset": 20, "axis": "v", "axis_origin": [20, 0],
 "angle": 360, "operation": "add",
 "profile": {"shape": "polygon", "points": [[28, 0], [40, 0], [40, 5], [32, 5], [32, 30], [28, 30]]}}
```
The axis lies in the sketch plane: it runs along `u` or `v` through `axis_origin`. The profile must stay
on one side of it.

### hole
```json
{"id": "bore", "type": "hole", "face": "top", "center": [40, 25], "diameter": 20, "through": true}
{"id": "screw", "type": "hole", "face": "top", "center": [10, 10], "diameter": 3.4, "depth": 8,
 "counterbore": {"diameter": 6.5, "depth": 3.5}}
{"id": "csk", "type": "hole", "face": "top", "center": [30, 10], "diameter": 4.5, "through": true,
 "countersink": {"diameter": 9, "angle": 90}}
{"id": "from_below", "type": "hole", "plane": "XY", "offset": 0, "direction": "positive",
 "center": [15, 15], "diameter": 8, "depth": 4}
```
Give exactly one of `face` or `plane` (plane + `offset` + drill `direction`), and exactly one of
`through: true` or `depth`. Through-holes cut across the whole part along w.

### slot
```json
{"id": "slot", "type": "slot", "face": "top", "center": [30, 17.5],
 "length": 20, "width": 6, "angle": 0, "through": true}
```
`length` is end to end. `angle` is measured from the u axis, counter-clockwise in (u, v): on the top face,
0 runs along X and 90 along Y.

### linear_pattern
```json
{"id": "corner_holes", "type": "linear_pattern", "feature": "corner_hole",
 "count_u": 2, "spacing_u": "width - 16", "count_v": 2, "spacing_v": "depth - 16"}
```
Repeats an earlier hole or slot on a grid in its own sketch plane. Counts include the original.

### circular_pattern
```json
{"id": "bolt_holes", "type": "circular_pattern", "feature": "bolt_hole", "center": [20, 20],
 "count": 4, "angle": 360}
```
With `angle: 360` the copies are spread evenly around; otherwise the first and last copies are
`angle` apart.

### fillet / chamfer
```json
{"id": "corners", "type": "fillet", "radius": 5, "edges": "vertical_outer"}
{"id": "top_edges", "type": "chamfer", "size": 1, "edges": "top_outer"}
{"id": "rim", "type": "fillet", "radius": 1, "edges": "top_of_feature:boss"}
```
Edge selectors:
- `vertical_outer`: straight edges parallel to Z on the outside of the part.
- `top_outer` / `bottom_outer`: outside edges of faces that point straight up or down.
- `all_outer`: every outside edge.
- `top_of_feature:<id>`: the edges a feature created at its open end. For a hole or slot that's the
  rim where it enters; for an added extrude it's the far cap; for a cut extrude it's the opening.

"Outside" means a convex edge that isn't on a hole or cutout outline. Concave corners (such as the inside
of an L), hole rims and tangent seams are never selected by the `*_outer` selectors.

### shell
```json
{"id": "hollow", "type": "shell", "thickness": 2, "open_faces": ["top"]}
```
Hollows the part inwards, keeping the outer surface. `open_faces` removes those bbox faces; with an
empty list the part becomes a closed hollow body. Shell before adding small features.

## Out of scope
Assemblies, sweeps, lofts, threads and freeform surfaces.

## What the compiler returns
- `solid` (CadQuery), `step` and `stl` bytes.
- `metrics`: volume, bbox, face/edge/triangle counts, resolved parameters, per-feature status with the
  volume each feature added or removed, silhouette framing, warnings.
- `silhouettes`: `top` (x right, y up), `front` (x, z) and `side` (y, z) binary masks at a fixed
  `mm_per_px` (default 0.2). Each carries `origin_mm`, the mm position of its lower-left corner, so a
  mask can be compared with an input mask in mm. A through-hole shows up as a hole; a blind hole does not.

## Errors
A spec that can't be built raises `SpecError`, whose `errors` are self-contained sentences that name
the feature and what to change, for example:

```
feature bore: centre (85, 10) on the top face is outside the part bbox in XY (80 x 50: x 0..80, y 0..50)
feature top_round: fillet radius 25 is larger than the wall it sits on (the face at (35, 0, 10) is only 20 mm across); use less than 20
feature corner_hole.diameter: unknown parameter 'hole_dia' in 'hole_dia' (known parameters: bore_d, depth, ...)
parameters: parameter cycle: depth -> width -> depth
```
Expression errors are all reported at once. Geometry stops at the first failing feature, and
`feature_status` marks the remaining features as skipped.
