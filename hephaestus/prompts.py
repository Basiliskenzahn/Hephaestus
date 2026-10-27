"""Prompts for Talos: reading a drawing into spec + callouts, and correcting a spec from check results."""
import json
import os

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fewshot", "pillow_block.json")) as f:
    FEWSHOT = json.load(f)

_SPEC_GUIDE = """## Coordinates
- All values in mm, angles in degrees.
- Origin = lower-left-front corner of the part's bounding box. X right, Y back, Z up. Nothing may be at negative coordinates.
- Drawing views: TOP shows X (right) and Y (up = back). FRONT shows X (right) and Z (up). SIDE shows Y (right = back) and Z (up).
- Sketch planes use 2D coordinates (u, v); w is the plane's normal axis:
  XY: u = x, v = y, extrudes along +z.  XZ: u = x, v = z, extrudes along +y.  YZ: u = y, v = z, extrudes along +x.

## Parameters and expressions
- Put every dimension written on the drawing into `parameters` with a meaningful name and a short description:
  width, depth, thickness, hole_dia, hole_inset, hole_pitch, slot_len, slot_w, corner_r, ...
- Every number in the features is either a number or an expression over parameters ("width - 2 * hole_inset",
  "depth / 2"). Expressions allow only numbers, parameter names, + - * / and parentheses.
- Write positions as expressions when the drawing implies a relationship: something drawn centred is at "width / 2".
- Inset vs pitch: when the drawing gives both an inset from an edge and a spacing between features, derive the
  spacing from the inset ("length - 2 * hole_inset") instead of making it a separate parameter, unless a note says
  the pitch is fixed. Record the intent in the parameter description ("holes stay 8 from the edges").
- Use only dimensions written on the drawing or following directly from them. If you assume something (e.g. a hole
  is centred because it is drawn on the centreline), say so in the spec `description`.

## Features (applied in order; the first must add material)
- extrude: a 2D profile on a plane (XY/XZ/YZ at `offset`), pushed `distance` along the plane's normal
  (direction positive/negative/symmetric), operation add or cut. Model an L-bracket as two overlapping extrudes.
  Profiles: rect (u, v = lower-left corner, width along u, height along v; anchor "center" makes (u, v) the centre),
  rounded_rect (+ radius), circle (u, v centre, diameter), polygon (points), path (start + line/arc segments).
- revolve: a profile on a plane revolved about a line in that plane (axis "u" or "v" through axis_origin).
- hole: centre (u, v) on a face, diameter, `through: true` or a `depth`, optional counterbore or countersink.
  `face` is a face of the whole part's bounding box: top/bottom (XY), front/back (XZ), left/right (YZ).
  To start on a lower face, name a feature: "top_of_feature:base" is the top of the extrude with id "base", even if
  something taller stands next to it. Through-holes cut across the whole part.
- slot: like a hole, with `length` (overall, end to end), `width` and `angle` (0 = along u, 90 = along v).
  A slot's direction follows its length dimension: if the length is measured along X, the angle is 0 on the top face.
- linear_pattern: repeats an earlier hole or slot on a grid: count_u/spacing_u, count_v/spacing_v (counts include
  the original). circular_pattern: count copies around a centre.
- fillet (radius) / chamfer (size) on edges: "vertical_outer" (vertical outside corners), "top_outer", "bottom_outer",
  "all_outer", or "top_of_feature:<id>" (the rim/top edges that feature created). "1 x 45°" is a chamfer of size 1.
- shell: hollow the part with wall `thickness`, opening `open_faces` (e.g. ["top"]).
- Feature ids are short snake_case names.

## Callouts
List every dimension written on the drawing once in `callouts` (a "4x Ø6" is one callout), with its value and how to
measure it on the built part, so the part can be checked against your reading:
- overall (axis): bounding-box size. thickness (feature): smallest size of an added extrude/revolve, or a shell's wall.
- diameter / radius (feature): of a hole, circle, revolve, fillet or rounded corner.
- distance (feature, feature_b, axis): between two centres. distance_to_edge (feature, edge e.g. "x_min"): from a
  centre to a side of the part.
- slot_length (feature, axis = the direction its length dimension runs) / slot_width (feature). chamfer_size
  (feature). depth (feature): of a blind hole, slot or pocket.
- unchecked: anything else (angles, notes, widths of a sub-feature, counterbore depths).
- distance and distance_to_edge always measure from a feature's centre: a hole's axis, a slot's or pocket's centre.
- Also add a callout for each position the drawing implies but does not dimension (a hole drawn on the centreline,
  a pocket drawn centred): label it "<feature> centred" and give the implied value of its centre, e.g.
  distance_to_edge from x_min = width / 2. A through hole has no position along its own axis: never add a callout
  for that direction.
- Every callout except unchecked needs its fields: overall needs axis; distance needs feature, feature_b and axis;
  distance_to_edge needs feature and edge; the others need feature.
Refer to pattern copies as "<pattern_id>[k]": [0] is the original, linear copies run along u first, then v."""

_EXAMPLE = ("## Example\nDrawing:\n" + FEWSHOT["drawing"] + "\nOutput:\n"
            + json.dumps({"spec": FEWSHOT["spec"], "callouts": FEWSHOT["callouts"]}, separators=(",", ":")))

READ_PROMPT = ("You convert dimensioned engineering drawings into a parametric part spec (JSON, spec v1) plus the "
               "list of dimensions you read.\nA deterministic CAD compiler builds the part from your spec, so it must "
               "be exact.\n\n" + _SPEC_GUIDE + "\n\n" + _EXAMPLE + "\n\nReturn only the JSON.")

CORRECT_PROMPT = ("You fix a parametric part spec (JSON, spec v1) that was read from an engineering drawing. The part "
                  "was built and checked; the checks found problems.\n\n" + _SPEC_GUIDE + "\n\n## Correcting\n"
                  "- The callouts are fixed: they are the dimensions read from the drawing, and the part must satisfy "
                  "them. You change the spec, not the callouts.\n"
                  "- Look at the drawing again for every issue. Fix what the issue points at and keep everything that "
                  "was right.\n- A visual-check issue can be wrong. A callout measurement is exact: if a visual issue "
                  "contradicts a callout that passes, keep the spec and say so in the changelog.\n"
                  "- Never invent a dimension. Every feature, vertex or position you add must be located by "
                  "dimensions written on the drawing. If fixing an issue would need a size the drawing does not give, "
                  "keep the spec and say so in the changelog.\n"
                  "- Only if you are sure a callout itself was misread from the drawing, list it in "
                  "`disputed_callouts` (its index, the reason, the corrected callout). It is replaced only if an "
                  "independent check agrees; otherwise it keeps applying.\n"
                  "- Return the full corrected spec, `disputed_callouts` (usually []) and a one-line changelog.\n\n"
                  "## Example of a spec read from a drawing\n" + _EXAMPLE.split("\n", 1)[1] + "\n\nReturn only the JSON.")


def correction_request(spec, callouts, issues):
    """User text for a correction call: the spec, the fixed callouts (numbered) and the numbered issue list."""
    lines = [f"{i}. [{iss['source']}] {iss['message']}" for i, iss in enumerate(issues, 1)]
    fixed = [f"[{k}] " + json.dumps(c, separators=(",", ":"), ensure_ascii=False) for k, c in enumerate(callouts)]
    return ("Spec:\n" + json.dumps(spec, separators=(",", ":"))
            + "\n\nFixed callouts (read from the drawing; the part must satisfy them):\n" + "\n".join(fixed)
            + "\n\nIssues found when building and checking the spec:\n" + "\n".join(lines)
            + "\n\nReturn the corrected spec for the drawing in the image.")
