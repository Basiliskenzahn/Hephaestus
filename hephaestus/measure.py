"""Evaluate drawing callouts on a compiled part.

A callout is a dimension the model read off the drawing plus a recipe for measuring it (see spec.Callout).
Measuring it on the compiled geometry checks the model's spec against its own reading of the drawing, without
any ground truth. Each mismatch becomes one self-contained message for the correction prompt.

Measurements:
  overall(axis)                  bbox size
  diameter(feature)              cylindrical faces coaxial with the feature (hole, circle extrude, revolve)
  radius(feature)                fillet / rounded corner: convex cylinder or torus faces; holes: diameter / 2
  distance(feature, b, axis)     between the two centres along axis
  distance_to_edge(feature, edge) from the centre to a bbox face, e.g. edge "x_min"
  slot_length / slot_width       from the slot's two end cylinders
  thickness(feature)             smallest size of an added extrude/revolve body; a shell's wall thickness
  chamfer_size(feature)          45-degree flat faces, size along the edge's two axes
  depth(feature)                 blind hole / slot / pocket: from its entry face to the first material along its axis
"""
import re

import cadquery as cq
import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp_Face
from OCP.gp import gp_Pnt, gp_Vec

TOL = 0.5
AXES = {"x": 0, "y": 1, "z": 2}


class CalloutError(ValueError):
    pass


class CalloutIncomplete(CalloutError):
    """The callout itself is malformed (missing fields); it says nothing about the part."""


def describe(c):
    m = c["measure"]
    if m == "overall":
        return f"overall size along {c.get('axis')}"
    if m == "distance":
        return f"distance {c.get('feature')} → {c.get('feature_b')} along {c.get('axis')}"
    if m == "distance_to_edge":
        return f"distance from {c.get('feature')} to {c.get('edge')}"
    return f"{m.replace('_', ' ')} of {c.get('feature')}"


def _fmt(x):
    return f"{x:.3g}" if abs(x - round(x)) > 1e-6 else str(int(round(x)))


# ---------------------------------------------------------------- geometry helpers
def _cylinders(solid):
    """[(radius, axis point, axis dir, convex)] for cylindrical faces, plus tori as (minor radius, ...)."""
    out = []
    for face in solid.Faces():
        kind = face.geomType()
        if kind not in ("CYLINDER", "TORUS"):
            continue
        ad = BRepAdaptor_Surface(face.wrapped)
        if kind == "CYLINDER":
            g = ad.Cylinder()
            r = g.Radius()
        else:
            g = ad.Torus()
            r = g.MinorRadius()
        loc, d = g.Location(), g.Axis().Direction()
        p0, dv = np.array([loc.X(), loc.Y(), loc.Z()]), np.array([d.X(), d.Y(), d.Z()])
        um, vm = (ad.FirstUParameter() + ad.LastUParameter()) / 2, (ad.FirstVParameter() + ad.LastVParameter()) / 2
        pnt, vec = gp_Pnt(), gp_Vec()
        BRepGProp_Face(face.wrapped).Normal(um, vm, pnt, vec)       # respects face orientation: points out of the solid
        pc, n = np.array([pnt.X(), pnt.Y(), pnt.Z()]), np.array([vec.X(), vec.Y(), vec.Z()])
        radial = (pc - p0) - np.dot(pc - p0, dv) * dv
        convex = float(np.dot(n, radial)) > 0 if kind == "CYLINDER" else True
        out.append((r, p0, dv, convex, kind))
    return out


def _line_dist(p, p0, d):
    v = np.asarray(p) - p0
    return float(np.linalg.norm(v - np.dot(v, d) * d))


def _coaxial(cyls, center, axis):
    axis = np.asarray(axis, float)
    return [c for c in cyls if c[4] == "CYLINDER" and abs(abs(np.dot(c[2], axis)) - 1) < 1e-6
            and _line_dist(center, c[1], c[2]) < 0.01]


# ---------------------------------------------------------------- references
def resolve(ref, geometry):
    """'hole_1' or 'corner_holes[2]' -> (geometry entry, centre or None)."""
    if not ref:
        raise CalloutIncomplete("needs a feature id")
    m = re.fullmatch(r"([A-Za-z][A-Za-z0-9_\-]*)(?:\[(\d+)\])?", ref.strip())
    if not m:
        raise CalloutIncomplete(f"'{ref}' is not a feature reference (use an id or '<pattern_id>[k]')")
    fid, k = m.group(1), m.group(2)
    if fid not in geometry:
        raise CalloutError(f"feature '{fid}' does not exist (features: {', '.join(geometry)})")
    g = geometry[fid]
    if g["type"] in ("linear_pattern", "circular_pattern"):
        insts = geometry[g["source"]]["instances"] + g["instances"]
        k = int(k or 0)
        if k >= len(insts):
            raise CalloutError(f"'{ref}': pattern {fid} has {len(insts)} copies, numbered 0 to {len(insts) - 1}")
        return g, np.array(insts[k]["center"])
    if k not in (None, "0"):
        raise CalloutError(f"'{ref}': {fid} is a {g['type']}, not a pattern; refer to it as '{fid}'")
    insts = g.get("instances")
    if insts:
        return g, np.array(insts[0]["center"])
    if "bbox" in g:
        lo, hi = np.array(g["bbox"][0]), np.array(g["bbox"][1])
        return g, (lo + hi) / 2
    return g, None


# ---------------------------------------------------------------- one callout
def measure_one(c, result, cyls):
    """Returns the measured value (or list of candidate values; the closest counts). Raises CalloutError."""
    m = c["measure"]
    lo, hi = (np.array(result.metrics["bbox"][k]) for k in ("min", "max"))
    geo = result.geometry

    def need(*names):
        missing = [n for n in names if not c.get(n)]
        if missing:
            raise CalloutIncomplete(f"measure '{m}' needs {', '.join(missing)}")

    if m == "overall":
        need("axis")
        a = AXES[c["axis"]]
        return hi[a] - lo[a]
    if m in ("distance", "distance_to_edge"):
        need("feature", "axis" if m == "distance" else "edge")
        _, ca = resolve(c["feature"], geo)
        if ca is None:
            raise CalloutError(f"'{c['feature']}' has no position to measure from")
        if m == "distance":
            need("feature_b")
            _, cb = resolve(c["feature_b"], geo)
            if cb is None:
                raise CalloutError(f"'{c['feature_b']}' has no position to measure from")
            a = AXES[c["axis"]]
            return abs(ca[a] - cb[a])
        a = AXES[c["edge"][0]]
        edge = lo[a] if c["edge"].endswith("min") else hi[a]
        return abs(ca[a] - edge)
    need("feature")
    g, center = resolve(c["feature"], geo)
    t = g["type"]
    if m in ("diameter", "radius") and t in ("hole", "linear_pattern", "circular_pattern", "extrude", "revolve") \
            and center is not None and g.get("axis") and (t != "extrude" or "diameter" in g):
        found = sorted({round(2 * r, 6) for r, *_ in _coaxial(cyls, center, g["axis"])})
        if not found:
            raise CalloutError(f"no cylindrical surface of {c['feature']} found on the part")
        return [d / (2 if m == "radius" else 1) for d in found]
    if m == "radius":
        # a fillet rounds outside edges (convex faces); a pocket or rounded profile may have concave corners
        radii = sorted({round(r, 6) for r, _, _, convex, _ in cyls if convex or t != "fillet"})
        if not radii:
            raise CalloutError("the part has no rounded edges or corners")
        return radii
    if m in ("slot_length", "slot_width"):
        if "length" not in g:
            raise CalloutError(f"'{c['feature']}' is a {t}, not a slot")
        n = np.asarray(g["axis"], float)
        L = g["length"]
        ends = [cy for cy in cyls if cy[4] == "CYLINDER" and abs(abs(np.dot(cy[2], n)) - 1) < 1e-6
                and _line_dist(center, cy[1], cy[2]) < L / 2 + 1]
        if not ends:
            raise CalloutError(f"no slot ends of {c['feature']} found on the part")
        r = min(e[0] for e in ends)
        pts = [e[1] - np.dot(e[1] - center, n) * n for e in ends if abs(e[0] - r) < 1e-6]
        sep = max(float(np.linalg.norm(p - q)) for p in pts for q in pts)
        return (sep + 2 * r) if m == "slot_length" else 2 * r
    if m == "thickness":
        if t == "shell":
            return g["thickness"]
        if "bbox" not in g:
            raise CalloutError(f"thickness needs an added extrude/revolve or a shell, '{c['feature']}' is a {t}")
        size = np.array(g["bbox"][1]) - np.array(g["bbox"][0])
        return float(size.min())
    if m == "depth":
        return _depth(c["feature"], g, center, result, lo, hi)
    if m == "chamfer_size":
        sizes = []
        for face in result.solid.Faces():
            if face.geomType() != "PLANE":
                continue
            nrm = np.abs(face.normalAt(face.Center()).toTuple())
            on = [i for i in range(3) if nrm[i] > 1e-6]
            if len(on) == 2 and all(abs(nrm[i] - 0.7071) < 0.01 for i in on):
                bb = face.BoundingBox()
                ext = np.array([bb.xlen, bb.ylen, bb.zlen])
                sizes.append(round(float(np.mean(ext[on])), 6))
        if not sizes:
            raise CalloutError("the part has no 45-degree chamfer faces")
        return sorted(set(sizes))
    raise CalloutError(f"cannot measure {m} of a {t}")


def _first_material(solid, start, direction, length):
    """Distance from `start` along `direction` to the first point inside the solid, or None."""
    p1 = start + direction * length
    hit = solid.intersect(cq.Edge.makeLine(cq.Vector(*start), cq.Vector(*p1)))
    ds = [float(np.dot(np.array(v.toTuple()) - start, direction)) for v in hit.Vertices()]
    return min(ds) if ds else None


def _depth(ref, g, center, result, lo, hi):
    t = g["type"]
    axis = np.asarray(g.get("axis") or [0, 0, 1], float)
    span = float(np.max(hi - lo)) + 2
    if t in ("hole", "slot", "linear_pattern", "circular_pattern"):
        # the centre lies on the entry plane; the drill goes whichever way along the axis stays inside the bbox
        def in_box(p):
            return bool(np.all(p >= lo - 1e-6) and np.all(p <= hi + 1e-6))
        into = next((d for d in (-axis, axis) if in_box(center + d * 0.01)), -axis)
        dist = _first_material(result.solid, center, into, span)
        if dist is None:
            raise CalloutError(f"{ref} goes all the way through; it has no depth")
        return dist
    if t == "extrude" and g.get("operation") == "cut":
        blo, bhi = np.array(g["bbox"][0]), np.array(g["bbox"][1])
        a = int(np.argmax(np.abs(axis)))
        mid = (blo + bhi) / 2
        # the opening is the tool face that lies on (or beyond) the part's outside
        if bhi[a] >= hi[a] - 1e-6:
            start, into = mid.copy(), -np.eye(3)[a]
            start[a] = hi[a]
        else:
            start, into = mid.copy(), np.eye(3)[a]
            start[a] = lo[a]
        dist = _first_material(result.solid, start, into, span)
        if dist is None:
            raise CalloutError(f"{ref} cuts all the way through; it has no depth")
        return dist
    raise CalloutError(f"depth needs a hole, slot or cut extrude, '{ref}' is a {t}")


def check(callouts, result, tol=TOL):
    """Evaluate callouts (dicts or Callout models). Returns a list of
    {label, value, measure, status: ok|mismatch|error|invalid|unchecked, measured, message}.
    mismatch and error (e.g. the feature does not exist) are issues; invalid (malformed callout) is not."""
    cyls = _cylinders(result.solid)
    out = []
    for c in callouts:
        c = c.model_dump() if hasattr(c, "model_dump") else dict(c)
        c.setdefault("label", "?")
        c.setdefault("measure", "unchecked")
        row = {"label": c["label"], "value": c.get("value"), "measure": c["measure"], "what": describe(c)}
        if c["measure"] == "unchecked" or not isinstance(c.get("value"), (int, float)):
            out.append({**row, "status": "unchecked"})
            continue
        try:
            got = measure_one(c, result, cyls)
        except CalloutIncomplete as e:
            # a malformed callout is not evidence against the part: report it, don't ask for a correction
            out.append({**row, "status": "invalid",
                        "message": f"callout '{c['label']}' ({describe(c)}): {e}"})
            continue
        except CalloutError as e:
            out.append({**row, "status": "error",
                        "message": f"callout '{c['label']}' ({describe(c)}): {e}"})
            continue
        values = got if isinstance(got, list) else [got]
        best = min(values, key=lambda v: abs(v - c["value"]))
        ok = abs(best - c["value"]) <= tol
        row.update(status="ok" if ok else "mismatch", measured=round(float(best), 3))
        if not ok:
            row["message"] = (f"callout '{c['label']}' ({describe(c)}) measures {_fmt(best)} on the part, "
                              f"expected {_fmt(c['value'])}")
        out.append(row)
    return out


def issues(rows):
    """The messages of failed callouts."""
    return [r["message"] for r in rows if r["status"] in ("mismatch", "error")]
