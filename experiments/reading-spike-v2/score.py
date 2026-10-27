"""Score a predicted spec v1 by its compiled geometry, so equivalent specs count as correct.

Checks: compiles; bbox within 0.5 mm per axis; volume IoU with the true solid >= 0.98; every dimension written
on the drawing (ground_truth.json) measured on the compiled part within 0.5 mm. Also reports how the spec is
parametrised and whether changing its overall-X parameter gives the same part as changing the true one.

Dimension check kinds (points are (u, v) perpendicular to `axis`: z -> (x, y), y -> (x, z), x -> (y, z)):
  extent   bbox size along axis
  cyl      a cylindrical face with that axis, centre within 0.5 of `at`, diameter within 0.5 of `dia`
  spacing  distance along u or v between the cylinders nearest to `a` and `b`
  inset    distance along u or v from the bbox minimum to the cylinder nearest to `at`
  slot     the pair of equal-radius cylinders nearest to `at`: length (end to end, along `along`), width, inset_u/v
  probe    cast a ray along `dir` (e.g. "-z") through `at`; first surface hit, measured from the bbox min or max
  chamfer  45-degree flat faces; their height
"""
import json
import os
import sys

import cadquery as cq
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from hephaestus.compile import NON_NUMERIC, SpecError, compile  # noqa: E402

TOL = 0.5
IOU_MIN = 0.98
SEARCH = 5.0          # mm: how far from the expected point a cylinder may be to count as "the" hole
AXES = {"x": 0, "y": 1, "z": 2}
UV = {"z": (0, 1), "y": (0, 2), "x": (1, 2)}

with open(os.path.join(HERE, "ground_truth.json")) as f:
    GROUND_TRUTH = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

_true_cache = {}


def true_result(part, overrides=None):
    key = (part, json.dumps(overrides, sort_keys=True))
    if key not in _true_cache:
        _true_cache[key] = compile(os.path.join(ROOT, GROUND_TRUTH[part]["spec"]), overrides)
    return _true_cache[key]


# ---------------------------------------------------------------- measurements
def cylinders(solid):
    """[(axis name, centre (u, v), radius)] for cylindrical faces whose axis is parallel to X, Y or Z."""
    out = []
    for face in solid.Faces():
        if face.geomType() != "CYLINDER":
            continue
        cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
        d = cyl.Axis().Direction()
        direction = np.abs([d.X(), d.Y(), d.Z()])
        if direction.max() < 0.999:
            continue
        axis = "xyz"[int(direction.argmax())]
        loc = cyl.Location()
        p = (loc.X(), loc.Y(), loc.Z())
        i, j = UV[axis]
        out.append((axis, np.array([p[i], p[j]]), cyl.Radius()))
    return out


def nearest(cyls, axis, at, max_dist=SEARCH):
    best = None
    for a, c, r in cyls:
        if a != axis:
            continue
        dist = float(np.linalg.norm(c - np.asarray(at, float)))
        if dist <= max_dist and (best is None or dist < best[0]):
            best = (dist, c, r)
    return best


def probe(solid, direction, at, bbox, segment=False):
    sign, axis = (1 if direction[0] == "+" else -1), AXES[direction[1]]
    i, j = [k for k in range(3) if k != axis]
    p0, p1 = [0.0] * 3, [0.0] * 3
    p0[i] = p1[i] = at[0]
    p0[j] = p1[j] = at[1]
    lo, hi = bbox[0][axis] - 1, bbox[1][axis] + 1
    p0[axis], p1[axis] = (lo, hi) if sign > 0 else (hi, lo)
    hit = solid.intersect(cq.Edge.makeLine(cq.Vector(*p0), cq.Vector(*p1)))
    coords = [v.toTuple()[axis] for v in hit.Vertices()]
    if not coords:
        return None
    if segment:
        # length of the first stretch of material the ray passes through
        spans = sorted((sorted((e.startPoint().toTuple()[axis], e.endPoint().toTuple()[axis])) for e in hit.Edges()),
                       key=lambda sp: sp[0] * sign)
        first = spans[0] if sign > 0 else max(spans, key=lambda sp: sp[1])
        return first[1] - first[0]
    return min(coords) if sign > 0 else max(coords)


def measure(dim, solid, cyls, bbox):
    """Returns (measured value or None, note)."""
    kind = dim["check"]
    lo, hi = bbox
    if kind == "extent":
        a = AXES[dim["axis"]]
        return hi[a] - lo[a], ""
    if kind == "cyl":
        # among the cylinders on this axis line (a bushing has several), take the best-matching diameter
        on_axis = [(c, r) for a, c, r in cyls if a == dim["axis"] and np.linalg.norm(c - np.asarray(dim["at"], float)) <= TOL]
        hit = min(on_axis, key=lambda cr: abs(2 * cr[1] - dim["dia"])) if on_axis else None
        if hit is None:
            near = nearest(cyls, dim["axis"], dim["at"], max_dist=1e9)
            note = (f"nearest {dim['axis']}-cylinder is Ø{2 * near[2]:.2f} at {np.round(near[1], 2).tolist()}"
                    if near else f"no cylinder along {dim['axis']}")
            return None, note
        return 2 * hit[1], ""
    if kind in ("spacing", "inset"):
        idx = 0 if dim["along"] == "u" else 1
        axis_idx = UV[dim["axis"]][idx]
        if kind == "spacing":
            a, b = nearest(cyls, dim["axis"], dim["a"]), nearest(cyls, dim["axis"], dim["b"])
            if a is None or b is None:
                return None, f"no hole within {SEARCH} mm of {dim['a'] if a is None else dim['b']}"
            return abs(b[1][idx] - a[1][idx]), ""
        a = nearest(cyls, dim["axis"], dim["at"])
        if a is None:
            return None, f"no hole within {SEARCH} mm of {dim['at']}"
        return a[1][idx] - lo[axis_idx], ""
    if kind == "slot":
        cands = [(c, r) for ax, c, r in cyls if ax == dim["axis"]
                 and np.linalg.norm(c - np.asarray(dim["at"], float)) < 20]
        pairs = [(np.linalg.norm((c1 + c2) / 2 - dim["at"]), c1, c2, r1)
                 for k, (c1, r1) in enumerate(cands) for c2, r2 in cands[k + 1:] if abs(r1 - r2) < 1e-6]
        if not pairs:
            return None, "no slot (pair of equal end radii) near the expected position"
        _, c1, c2, r = min(pairs, key=lambda p: p[0])
        mid = (c1 + c2) / 2
        u_idx, v_idx = UV[dim["axis"]]
        sep = abs((c2 - c1)[0 if dim.get("along", "u") == "u" else 1])     # length is measured along the drawn direction
        values = {"length": float(sep) + 2 * r, "width": 2 * r,
                  "inset_u": mid[0] - lo[u_idx], "inset_v": mid[1] - lo[v_idx]}
        return values[dim["measure"]], ""
    if kind == "probe":
        hit = probe(solid, dim["dir"], dim["at"], bbox, segment=dim["measure"] == "segment")
        if dim["value"] is None:                      # expect a gap (e.g. a slit) here
            return (None, "") if hit is None else (hit, "expected no material here")
        if hit is None:
            return None, "ray misses the part"
        if dim["measure"] == "segment":
            return hit, ""
        a = AXES[dim["dir"][1]]
        return (hit - lo[a]) if dim["measure"] == "from_min" else (hi[a] - hit), ""
    if kind == "chamfer":
        heights = []
        for face in solid.Faces():
            if face.geomType() == "PLANE":
                nz = abs(face.normalAt(face.Center()).z)
                if 0.6 < nz < 0.8:
                    bb = face.BoundingBox()
                    heights.append(bb.zmax - bb.zmin)
        if not heights:
            return None, "no 45-degree faces"
        return max(heights), ""
    raise ValueError(f"unknown check {kind}")


# ---------------------------------------------------------------- parametrisation
def expression_stats(spec_dict):
    """How the features use parameters: numeric fields given as expressions, and how many of them compute something."""
    exprs, numbers = [], 0

    def walk(node, key=None):
        nonlocal numbers
        if isinstance(node, dict):
            for k, v in node.items():
                if k not in NON_NUMERIC:
                    walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            exprs.append(node)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            numbers += 1

    walk(spec_dict.get("features", []))
    computed = [e for e in exprs if any(op in e for op in "+-*/")]
    return {"parameters": len(spec_dict.get("parameters", {})), "expr_fields": len(exprs),
            "computed_exprs": len(computed), "literal_numbers": numbers, "examples": computed[:4]}


def x_param_check(part, pred_spec, pred_result, delta=20.0):
    """Widen the part through the predicted spec's X parameter; compare with the true spec widened the same way."""
    x_size = pred_result.metrics["bbox"]["size"][0]
    for name, value in pred_result.params.items():
        if abs(value - x_size) > TOL:
            continue
        try:
            wide = compile(pred_spec, {name: value + delta})
        except SpecError as e:
            return {"param": name, "ok": False, "note": f"does not compile when widened: {e.errors[0]}"}
        if abs(wide.metrics["bbox"]["size"][0] - (x_size + delta)) > TOL:
            continue
        truth = true_result(part, {GROUND_TRUTH[part]["x_param"]: true_result(part).params[GROUND_TRUTH[part]["x_param"]] + delta})
        iou = volume_iou(wide.solid, truth.solid)
        return {"param": name, "iou_after_change": round(iou, 4), "ok": bool(iou >= IOU_MIN)}
    return {"param": None, "ok": False, "note": "no parameter drives the overall X size"}


def volume_iou(a, b):
    inter = a.intersect(b).Volume()
    union = a.Volume() + b.Volume() - inter
    return inter / union if union > 0 else 0.0


# ---------------------------------------------------------------- main entry
def score(part, pred_spec):
    """Score one predicted spec (dict) against ground-truth part key, e.g. '01_plate'."""
    stats = expression_stats(pred_spec) if isinstance(pred_spec, dict) else {}
    try:
        pred = compile(pred_spec)
    except SpecError as e:
        return {"compiles": False, "errors": e.errors, "perfect": False, "params": stats}
    truth = true_result(part)
    size_p, size_t = np.array(pred.metrics["bbox"]["size"]), np.array(truth.metrics["bbox"]["size"])
    bbox_ok = bool(np.all(np.abs(size_p - size_t) <= TOL))
    iou = volume_iou(pred.solid, truth.solid)
    bbox = (np.array(pred.metrics["bbox"]["min"]), np.array(pred.metrics["bbox"]["max"]))
    cyls = cylinders(pred.solid)
    dims = []
    for d in GROUND_TRUTH[part]["dimensions"]:
        expected = d["value"] if d["check"] != "cyl" else d["dia"]
        got, note = measure(d, pred.solid, cyls, bbox)
        ok = bool(got is None and not note) if expected is None else bool(got is not None and abs(got - expected) <= TOL)
        dims.append({"label": d["label"], "ok": ok, "expected": expected,
                     "measured": None if got is None else round(float(got), 3), **({"note": note} if note else {})})
    n_ok = sum(d["ok"] for d in dims)
    perfect = bool(bbox_ok and iou >= IOU_MIN and n_ok == len(dims))
    return {"compiles": True, "bbox_ok": bbox_ok, "bbox": size_p.round(3).tolist(), "iou": round(iou, 4),
            "dims_ok": f"{n_ok}/{len(dims)}", "perfect": perfect,
            "failed_dims": [d for d in dims if not d["ok"]], "warnings": pred.warnings,
            "params": stats, "x_param": x_param_check(part, pred_spec, pred)}


if __name__ == "__main__":
    # sanity check: every true spec scores perfect against itself
    for part, gt in GROUND_TRUTH.items():
        with open(os.path.join(ROOT, gt["spec"])) as f:
            s = score(part, json.load(f))
        print(part, json.dumps({k: s[k] for k in ("perfect", "iou", "dims_ok", "failed_dims", "x_param")}))
