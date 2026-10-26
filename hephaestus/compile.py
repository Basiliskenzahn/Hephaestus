"""Deterministic compiler: part spec v1 -> CadQuery solid, STEP/STL bytes, metrics and silhouettes.

Usage:
  python -m hephaestus.compile examples/plate.json --out out/
  python -m hephaestus.compile examples/plate.json --out out/ --set width=100 --set hole_inset=10

Every problem is reported as a SpecError whose messages name the feature and say what to change; the
verification loop feeds these back to the model as-is.
"""
import argparse
import json
import math
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field

import cadquery as cq
import numpy as np
from OCP.BRepTools import BRepTools
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape
from pydantic import ValidationError

from . import render
from .expr import ExprError, evaluate, resolve_parameters
from .spec import PartSpec, load

V = cq.Vector
TOL = 1e-6                       # geometric comparisons, mm
MESH_TOL, MESH_ANG = 0.01, 0.1   # tessellation: mm chord deviation, radians
OVERSHOOT = 1.0                  # cutting tools start this far outside the surface, mm

# (u, v, w) -> world axis index, per sketch plane
PLANE_AXES = {"XY": (0, 1, 2), "XZ": (0, 2, 1), "YZ": (1, 2, 0)}
# face name -> (plane, side of the bbox along w)
FACES = {"top": ("XY", "max"), "bottom": ("XY", "min"), "back": ("XZ", "max"), "front": ("XZ", "min"),
         "right": ("YZ", "max"), "left": ("YZ", "min")}
AXIS_NAMES = "xyz"
NON_NUMERIC = {"id", "type", "plane", "face", "direction", "operation", "anchor", "shape", "kind", "axis",
               "edges", "feature", "open_faces", "through", "clockwise"}


class SpecError(ValueError):
    """A spec that cannot be compiled. `errors` are self-contained messages, one per problem."""

    def __init__(self, errors, feature_status=None):
        self.errors = list(errors)
        self.feature_status = feature_status or []
        super().__init__("\n".join(self.errors))


@dataclass
class Result:
    spec: PartSpec
    params: dict
    solid: cq.Solid
    step: bytes
    stl: bytes
    metrics: dict
    silhouettes: dict
    warnings: list = field(default_factory=list)
    timings: dict = field(default_factory=dict)


# ---------------------------------------------------------------- small helpers
def fmt(x):
    return f"{x:.6g}" if abs(x - round(x)) > 1e-9 else str(int(round(x)))


def pt(p):
    return "(" + ", ".join(fmt(c) for c in p) + ")"


def world(plane, u, v, w):
    out = [0.0, 0.0, 0.0]
    iu, iv, iw = PLANE_AXES[plane]
    out[iu], out[iv], out[iw] = u, v, w
    return V(*out)


def axis_vec(plane, which):
    idx = PLANE_AXES[plane]["uvw".index(which)]
    return V(*[1.0 if i == idx else 0.0 for i in range(3)])


def bbox_of(shape):
    bb = shape.BoundingBox()
    return np.array([bb.xmin, bb.ymin, bb.zmin]), np.array([bb.xmax, bb.ymax, bb.zmax])


def in_plane(bbox, plane):
    """(u_range, v_range, w_range) of a world bbox seen in a sketch plane."""
    lo, hi = bbox
    return [(lo[i], hi[i]) for i in PLANE_AXES[plane]]


def single_solid(shape, fid, what):
    solids = shape.Solids()
    if len(solids) == 0:
        raise SpecError([f"feature {fid}: {what} leaves no material at all"])
    if len(solids) > 1:
        vols = sorted((s.Volume() for s in solids), reverse=True)
        raise SpecError([f"feature {fid}: {what} leaves {len(solids)} separate bodies (volumes "
                         + ", ".join(fmt(round(v, 1)) for v in vols) + " mm3); a part must be one connected solid"])
    return solids[0]


# ---------------------------------------------------------------- spec -> numbers
def _resolve_values(node, params, path, errors):
    if isinstance(node, dict):
        return {k: (v if k in NON_NUMERIC else _resolve_values(v, params, f"{path}.{k}", errors))
                for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve_values(v, params, f"{path}[{i}]", errors) for i, v in enumerate(node)]
    if isinstance(node, (str, int, float)) and not isinstance(node, bool):
        try:
            return evaluate(node, params)
        except ExprError as e:
            errors.append(f"{path}: {e}")
            return float("nan")
    return node


def resolve(spec, overrides=None):
    """Evaluate parameters (with overrides) and every expression in the features. Returns (params, features)."""
    errors = []
    raw = {name: p.value for name, p in spec.parameters.items()}
    for name, value in (overrides or {}).items():
        if name not in raw:
            errors.append(f"override: unknown parameter '{name}' (known parameters: {', '.join(sorted(raw))})")
        raw[name] = value
    try:
        params = resolve_parameters(raw)
    except ExprError as e:
        raise SpecError(errors + [f"parameters: {e}"]) from None
    for name, p in spec.parameters.items():
        if p.min is not None and params[name] < p.min - TOL:
            errors.append(f"parameter {name} = {fmt(params[name])} is below its minimum {fmt(p.min)}")
        if p.max is not None and params[name] > p.max + TOL:
            errors.append(f"parameter {name} = {fmt(params[name])} is above its maximum {fmt(p.max)}")
    feats = [_resolve_values(f.model_dump(), params, f"feature {f.id}", errors) for f in spec.features]
    if errors:
        raise SpecError(errors)
    return params, feats


def check_references(feats):
    errors, seen, op_of = [], {}, {}
    if feats[0]["type"] not in ("extrude", "revolve") or feats[0]["operation"] != "add":
        errors.append(f"feature {feats[0]['id']}: the first feature must create material "
                      "(an extrude or revolve with operation 'add')")
    for f in feats:
        fid, t = f["id"], f["type"]
        if t in ("linear_pattern", "circular_pattern"):
            src = seen.get(f["feature"])
            if src is None:
                errors.append(f"feature {fid}: pattern source '{f['feature']}' is not an earlier feature"
                              f" (earlier: {', '.join(seen) or 'none'})")
            elif src not in ("hole", "slot"):
                errors.append(f"feature {fid}: can only pattern a hole or slot, '{f['feature']}' is a {src}")
        if t in ("fillet", "chamfer") and f["edges"].startswith("top_of_feature:"):
            ref = f["edges"].split(":", 1)[1]
            if ref not in seen:
                errors.append(f"feature {fid}: edges '{f['edges']}' refers to '{ref}', which is not an earlier feature")
            elif seen[ref] in ("revolve", "fillet", "chamfer", "shell"):
                errors.append(f"feature {fid}: top_of_feature works for extrude, hole, slot and patterns, "
                              f"not for a {seen[ref]} ('{ref}')")
        if t in ("hole", "slot") and f["face"] and "_of_feature:" in f["face"]:
            ref = f["face"].split("_of_feature:", 1)[1]
            if ref not in seen:
                errors.append(f"feature {fid}: face '{f['face']}' refers to '{ref}', which is not an earlier feature"
                              f" (earlier: {', '.join(seen) or 'none'})")
            elif seen[ref] not in ("extrude", "revolve") or op_of[ref] != "add":
                errors.append(f"feature {fid}: face '{f['face']}' must refer to an added extrude or revolve; "
                              f"'{ref}' is a {seen[ref]}")
        seen[fid] = t
        op_of[fid] = f.get("operation")
    if errors:
        raise SpecError(errors)


# ---------------------------------------------------------------- profiles
def _edges_from_points(plane, w, pts2d):
    return [cq.Edge.makeLine(world(plane, *a, w), world(plane, *b, w))
            for a, b in zip(pts2d, pts2d[1:] + pts2d[:1]) if math.dist(a, b) > TOL]


def _arc_center_mid(start, center, end, clockwise, fid, k):
    r0, r1 = math.dist(start, center), math.dist(end, center)
    if abs(r0 - r1) > 1e-3:
        raise SpecError([f"feature {fid}: path segment {k} (arc_center) start {pt(start)} and end {pt(end)} are not "
                         f"the same distance from centre {pt(center)} ({fmt(r0)} vs {fmt(r1)})"])
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    if clockwise:
        a0, a1 = -a0, -a1
    sweep = (a1 - a0) % (2 * math.pi)
    if sweep < 1e-9:
        raise SpecError([f"feature {fid}: path segment {k} (arc_center) starts and ends at the same point"])
    am = a0 + sweep / 2
    if clockwise:
        am = -am
    return (center[0] + r0 * math.cos(am), center[1] + r0 * math.sin(am))


def profile_wire(prof, plane, w, fid):
    """Closed wire for a resolved profile, placed on `plane` at normal position w."""
    shape = prof["shape"]
    if shape in ("rect", "rounded_rect"):
        wd, ht = prof["width"], prof["height"]
        if wd <= 0 or ht <= 0:
            raise SpecError([f"feature {fid}: {shape} width and height must be positive (got {fmt(wd)} x {fmt(ht)})"])
        u0, v0 = prof["u"], prof["v"]
        if prof["anchor"] == "center":
            u0, v0 = u0 - wd / 2, v0 - ht / 2
        r = prof.get("radius", 0.0)
        if r < 0 or r > min(wd, ht) / 2 + TOL:
            raise SpecError([f"feature {fid}: corner radius {fmt(r)} must be between 0 and half the smaller side "
                             f"({fmt(min(wd, ht) / 2)})"])
        if r <= TOL:
            pts = [(u0, v0), (u0 + wd, v0), (u0 + wd, v0 + ht), (u0, v0 + ht)]
            return cq.Wire.assembleEdges(_edges_from_points(plane, w, pts))
        r = min(r, min(wd, ht) / 2)
        c = r * (1 - math.sqrt(0.5))
        u1, v1 = u0 + wd, v0 + ht
        P = lambda u, v: world(plane, u, v, w)
        corners = [((u0 + r, v0), (u1 - r, v0), (u1 - c, v0 + c), (u1, v0 + r)),
                   ((u1, v0 + r), (u1, v1 - r), (u1 - c, v1 - c), (u1 - r, v1)),
                   ((u1 - r, v1), (u0 + r, v1), (u0 + c, v1 - c), (u0, v1 - r)),
                   ((u0, v1 - r), (u0, v0 + r), (u0 + c, v0 + c), (u0 + r, v0))]
        edges = []
        for a, b, mid, end in corners:
            if math.dist(a, b) > TOL:
                edges.append(cq.Edge.makeLine(P(*a), P(*b)))
            edges.append(cq.Edge.makeThreePointArc(P(*b), P(*mid), P(*end)))
        return cq.Wire.assembleEdges(edges)
    if shape == "circle":
        if prof["diameter"] <= 0:
            raise SpecError([f"feature {fid}: circle diameter must be positive (got {fmt(prof['diameter'])})"])
        return cq.Wire.makeCircle(prof["diameter"] / 2, world(plane, prof["u"], prof["v"], w), axis_vec(plane, "w"))
    if shape == "polygon":
        pts = [tuple(p) for p in prof["points"]]
        return cq.Wire.assembleEdges(_edges_from_points(plane, w, pts))
    # path
    P = lambda p: world(plane, p[0], p[1], w)
    cur, start, edges = tuple(prof["start"]), tuple(prof["start"]), []
    for k, seg in enumerate(prof["segments"], 1):
        end = tuple(seg["to"])
        if math.dist(cur, end) < TOL:
            raise SpecError([f"feature {fid}: path segment {k} ({seg['kind']}) has zero length (ends where it starts, {pt(end)})"])
        if seg["kind"] == "line":
            edges.append(cq.Edge.makeLine(P(cur), P(end)))
        else:
            via = tuple(seg["via"]) if seg["kind"] == "arc3" else \
                _arc_center_mid(cur, tuple(seg["center"]), end, seg["clockwise"], fid, k)
            area = (via[0] - cur[0]) * (end[1] - cur[1]) - (via[1] - cur[1]) * (end[0] - cur[0])
            if abs(area) < 1e-9:
                raise SpecError([f"feature {fid}: path segment {k} (arc) points {pt(cur)}, {pt(via)}, {pt(end)} are collinear"])
            edges.append(cq.Edge.makeThreePointArc(P(cur), P(via), P(end)))
        cur = end
    if math.dist(cur, start) > TOL:
        edges.append(cq.Edge.makeLine(P(cur), P(start)))
    return cq.Wire.assembleEdges(edges)


def profile_face(prof, plane, w, fid):
    try:
        face = cq.Face.makeFromWires(profile_wire(prof, plane, w, fid))
    except SpecError:
        raise
    except Exception as e:  # noqa: BLE001 - OCC raises many types
        raise SpecError([f"feature {fid}: profile ({prof['shape']}) is not a valid closed outline: {e}"]) from None
    if not face.isValid() or face.Area() < TOL:
        raise SpecError([f"feature {fid}: profile ({prof['shape']}) is not a valid closed outline "
                         "(self-intersecting or zero area)"])
    return face


# ---------------------------------------------------------------- tools for holes and slots
def split_face(face):
    """'top' -> ('top', None); 'top_of_feature:base' -> ('top', 'base')."""
    if "_of_feature:" in face:
        name, ref = face.split("_of_feature:", 1)
        return name, ref
    return face, None


def placement(f, bbox, boxes):
    """Where a hole/slot starts: (plane, w_start, drill sign, description, bbox its centre must lie in)."""
    if f["face"]:
        name, ref = split_face(f["face"])
        plane, side = FACES[name]
        box = boxes[ref] if ref else bbox
        w_rng = in_plane(box, plane)[2]
        w_start = w_rng[1] if side == "max" else w_rng[0]
        where = f"the {name} face of feature {ref}" if ref else f"the {name} face"
        return plane, w_start, (-1.0 if side == "max" else 1.0), where, box
    sign = 1.0 if f["direction"] == "positive" else -1.0
    return f["plane"], f["offset"], sign, f"plane {f['plane']} at offset {fmt(f['offset'])}", bbox


def check_center(fid, center, plane, where, bbox, whose="the part"):
    (ulo, uhi), (vlo, vhi), _ = in_plane(bbox, plane)
    u, v = center
    if ulo - TOL <= u <= uhi + TOL and vlo - TOL <= v <= vhi + TOL:
        return
    un, vn = (AXIS_NAMES[i] for i in PLANE_AXES[plane][:2])
    raise SpecError([f"feature {fid}: centre {pt(center)} on {where} is outside {whose} bbox in {plane} "
                     f"({fmt(uhi - ulo)} x {fmt(vhi - vlo)}: {un} {fmt(ulo)}..{fmt(uhi)}, "
                     f"{vn} {fmt(vlo)}..{fmt(vhi)})"])


def _span(f, plane, w_start, sign, bbox, depth):
    """(w0, length) of a cutting tool along +w: through-cuts span the whole part, blind ones go `depth` in."""
    if f["through"]:
        lo, hi = in_plane(bbox, plane)[2]
        return lo - OVERSHOOT, (hi - lo) + 2 * OVERSHOOT
    a, b = w_start - sign * OVERSHOOT, w_start + sign * depth
    return min(a, b), abs(b - a)


def _cylinder(plane, u, v, w0, length, d):
    return cq.Solid.makeCylinder(d / 2, length, world(plane, u, v, w0), axis_vec(plane, "w"))


def hole_tool(f, bbox, boxes):
    fid = f["id"]
    plane, w_start, sign, where, box = placement(f, bbox, boxes)
    d = f["diameter"]
    if d <= 0:
        raise SpecError([f"feature {fid}: diameter must be positive (got {fmt(d)})"])
    if not f["through"] and f["depth"] <= 0:
        raise SpecError([f"feature {fid}: depth must be positive (got {fmt(f['depth'])})"])
    u, v = f["center"]
    check_center(fid, (u, v), plane, where, box, "the part" if box is bbox else "that feature's")
    w0, length = _span(f, plane, w_start, sign, bbox, f["depth"])
    tool = _cylinder(plane, u, v, w0, length, d)
    cb, cs = f.get("counterbore"), f.get("countersink")
    if cb:
        if cb["diameter"] <= d:
            raise SpecError([f"feature {fid}: counterbore diameter {fmt(cb['diameter'])} must be larger than the hole ({fmt(d)})"])
        if cb["depth"] <= 0:
            raise SpecError([f"feature {fid}: counterbore depth must be positive (got {fmt(cb['depth'])})"])
        a, b = w_start - sign * OVERSHOOT, w_start + sign * cb["depth"]
        tool = tool.fuse(_cylinder(plane, u, v, min(a, b), abs(b - a), cb["diameter"]))
    if cs:
        D, ang = cs["diameter"], cs["angle"]
        if D <= d:
            raise SpecError([f"feature {fid}: countersink diameter {fmt(D)} must be larger than the hole ({fmt(d)})"])
        if not 0 < ang < 180:
            raise SpecError([f"feature {fid}: countersink angle must be between 0 and 180 degrees (got {fmt(ang)})"])
        t = math.tan(math.radians(ang) / 2)
        h = (D - d) / 2 / t
        r_top = D / 2 + OVERSHOOT * t
        cone = cq.Solid.makeCone(r_top, d / 2, h + OVERSHOOT, world(plane, u, v, w_start - sign * OVERSHOOT),
                                 axis_vec(plane, "w") * sign)
        tool = tool.fuse(cone)
    cap = (world(plane, u, v, w_start), axis_vec(plane, "w"))
    return tool, plane, (u, v), cap


def slot_tool(f, bbox, boxes):
    fid = f["id"]
    plane, w_start, sign, where, box = placement(f, bbox, boxes)
    L, W = f["length"], f["width"]
    if W <= 0 or L < W - TOL:
        raise SpecError([f"feature {fid}: slot needs width > 0 and length >= width (got length {fmt(L)}, width {fmt(W)}); "
                         "length is the overall end-to-end length"])
    if not f["through"] and f["depth"] <= 0:
        raise SpecError([f"feature {fid}: depth must be positive (got {fmt(f['depth'])})"])
    cu, cv = f["center"]
    check_center(fid, (cu, cv), plane, where, box, "the part" if box is bbox else "that feature's")
    w0, length = _span(f, plane, w_start, sign, bbox, f["depth"])
    th = math.radians(f["angle"])
    e, n = (math.cos(th), math.sin(th)), (-math.sin(th), math.cos(th))
    half, r = (L - W) / 2, W / 2
    if half <= TOL:
        tool = _cylinder(plane, cu, cv, w0, length, W)
    else:
        P = lambda a, b: world(plane, cu + a * e[0] + b * n[0], cv + a * e[1] + b * n[1], w0)
        wire = cq.Wire.assembleEdges([
            cq.Edge.makeLine(P(-half, -r), P(half, -r)),
            cq.Edge.makeThreePointArc(P(half, -r), P(half + r, 0), P(half, r)),
            cq.Edge.makeLine(P(half, r), P(-half, r)),
            cq.Edge.makeThreePointArc(P(-half, r), P(-half - r, 0), P(-half, -r)),
        ])
        tool = cq.Solid.extrudeLinear(cq.Face.makeFromWires(wire), axis_vec(plane, "w") * length)
    cap = (world(plane, cu, cv, w_start), axis_vec(plane, "w"))
    return tool, plane, (cu, cv), cap


# ---------------------------------------------------------------- edges
def _edge_faces(solid):
    m = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(solid.wrapped, TopAbs_EDGE, TopAbs_FACE, m)
    return m


def _faces_of(edge, m):
    faces = []
    for f in m.FindFromKey(edge.wrapped):
        if not any(f.IsSame(g.wrapped) for g in faces):
            faces.append(cq.Face(f))
    return faces


def _in_inner_wire(edge, face):
    outer = cq.Wire(BRepTools.OuterWire_s(face.wrapped))
    if any(edge.wrapped.IsSame(e.wrapped) for e in outer.Edges()):
        return False
    return any(edge.wrapped.IsSame(e.wrapped) for e in face.Edges())


def _outer_info(solid, edge, faces):
    """(is_outer, normals): outer = convex, between two distinct non-tangent faces, not on any inner wire (hole rims)."""
    if len(faces) != 2 or any(_in_inner_wire(edge, f) for f in faces):
        return False, None
    mid = edge.positionAt(0.5)
    n1, n2 = (f.normalAt(mid) for f in faces)
    if n1.dot(n2) > 0.995:
        return False, None
    probe = mid + (n1 - n2) * 0.01
    return (not solid.isInside(probe)), (n1, n2)


def select_edges(solid, selector, records, fid):
    m = _edge_faces(solid)
    if selector.startswith("top_of_feature:"):
        ref = selector.split(":", 1)[1]
        tools, caps = records[ref]
        out = []
        for e in solid.Edges():
            mid = e.positionAt(0.5)
            if not any(abs((mid - p).dot(n)) < 1e-4 for p, n in caps):
                continue
            if any(t.distance(e) < 1e-4 for t in tools):
                out.append(e)
        return out
    zhat = V(0, 0, 1)
    out = []
    for e in solid.Edges():
        faces = _faces_of(e, m)
        outer, _ = _outer_info(solid, e, faces)
        if not outer:
            continue
        if selector == "all_outer":
            out.append(e)
        elif selector == "vertical_outer":
            if e.geomType() == "LINE" and abs(e.tangentAt(0.5).dot(zhat)) > 0.999:
                out.append(e)
        else:
            want = 1.0 if selector == "top_outer" else -1.0
            if any(f.geomType() == "PLANE" and f.normalAt(e.positionAt(0.5)).dot(zhat) * want > 0.999 for f in faces):
                out.append(e)
    return out


def smallest_wall(solid, edges):
    """Smallest width of a flat face next to a straight selected edge, measured across the face from the edge."""
    m = _edge_faces(solid)
    best = None
    for e in edges:
        if e.geomType() != "LINE":
            continue
        mid, t = e.positionAt(0.5), e.tangentAt(0.5)
        for f in _faces_of(e, m):
            if f.geomType() != "PLANE":
                continue
            d = f.normalAt(mid).cross(t).normalized()
            width = max(abs((vx.Center() - mid).dot(d)) for vx in f.Vertices())
            if best is None or width < best[0]:
                best = (width, f.Center())
    return best


# ---------------------------------------------------------------- features
def _combine(part, tool, op, fid, what):
    if op == "add":
        if part is None:
            return single_solid(tool, fid, what)
        new = single_solid(part.fuse(tool).clean(), fid, what)
        return new
    new = part.cut(tool).clean()
    if not new.Solids():
        raise SpecError([f"feature {fid}: {what} removes the whole part"])
    new = single_solid(new, fid, what)
    if part.Volume() - new.Volume() < 1e-6:
        raise SpecError([f"feature {fid}: {what} removes no material; it misses the part"])
    return new


def _extrude(f, part):
    fid, plane, d = f["id"], f["plane"], f["distance"]
    if d <= 0:
        raise SpecError([f"feature {fid}: distance must be positive (got {fmt(d)}); use 'direction' to flip it"])
    w0 = {"positive": f["offset"], "negative": f["offset"] - d, "symmetric": f["offset"] - d / 2}[f["direction"]]
    face = profile_face(f["profile"], plane, w0, fid)
    tool = cq.Solid.extrudeLinear(face, axis_vec(plane, "w") * d)
    end = w0 + d if f["direction"] != "negative" else w0
    if f["operation"] == "cut":
        end = f["offset"]
    cap = (world(plane, 0, 0, end), axis_vec(plane, "w"))
    return _combine(part, tool, f["operation"], fid, f"extrude ({f['operation']})"), ([tool], [cap])


def _revolve(f, part):
    fid, plane = f["id"], f["plane"]
    if not 0 < f["angle"] <= 360:
        raise SpecError([f"feature {fid}: revolve angle must be in (0, 360] degrees (got {fmt(f['angle'])})"])
    face = profile_face(f["profile"], plane, f["offset"], fid)
    au, av = f["axis_origin"]
    across = 0 if f["axis"] == "v" else 1          # coordinate measured across the axis
    ref = au if across == 0 else av
    iu = PLANE_AXES[plane][across]
    coords = [e.positionAt(t).toTuple()[iu] - ref for e in face.Edges() for t in np.linspace(0, 1, 17)]
    if min(coords) < -1e-6 and max(coords) > 1e-6:
        name = "uv"[across]
        raise SpecError([f"feature {fid}: the profile crosses the revolve axis ({f['axis']} axis through {pt((au, av))}); "
                         f"keep every point on one side ({name} >= {fmt(ref)} or {name} <= {fmt(ref)})"])
    start = world(plane, au, av, f["offset"])
    tool = cq.Solid.revolve(face, f["angle"], start, start + axis_vec(plane, f["axis"]))
    return _combine(part, tool, f["operation"], fid, f"revolve ({f['operation']})"), ([tool], [])


def _pattern(f, part, records, feats_by_id, bbox):
    fid, src = f["id"], feats_by_id[f["feature"]]
    tools, caps = records[src["id"]]
    base_tool, cap0 = tools[0], caps[0]
    plane = src["plane"] if src["plane"] else FACES[split_face(src["face"])[0]][0]
    U, Vv = axis_vec(plane, "u"), axis_vec(plane, "v")
    cu, cv = src["center"]
    copies = []
    if f["type"] == "linear_pattern":
        nu, nv = f["count_u"], f["count_v"]
        for name, n in (("count_u", nu), ("count_v", nv)):
            if n < 1 or abs(n - round(n)) > 1e-9:
                raise SpecError([f"feature {fid}: {name} must be a whole number >= 1 (got {fmt(n)})"])
        if round(nu) * round(nv) < 2:
            raise SpecError([f"feature {fid}: pattern makes no copies (count_u x count_v must be at least 2)"])
        for j in range(round(nv)):
            for i in range(round(nu)):
                if i == 0 and j == 0:
                    continue
                du, dv = i * f["spacing_u"], j * f["spacing_v"]
                off = U * du + Vv * dv
                copies.append((base_tool.translate(off), (cu + du, cv + dv), (cap0[0] + off, cap0[1])))
    else:
        n, total = f["count"], f["angle"]
        if n < 2 or abs(n - round(n)) > 1e-9:
            raise SpecError([f"feature {fid}: count must be a whole number >= 2 (got {fmt(n)})"])
        n = round(n)
        if not 0 < total <= 360:
            raise SpecError([f"feature {fid}: angle must be in (0, 360] degrees (got {fmt(total)})"])
        step = total / n if abs(total - 360) < 1e-9 else total / (n - 1)
        pu, pv = f["center"]
        centre = world(plane, pu, pv, 0)
        axis = U.cross(Vv)                          # counter-clockwise in (u, v)
        for k in range(1, n):
            a = step * k
            ca, sa = math.cos(math.radians(a)), math.sin(math.radians(a))
            c = (pu + (cu - pu) * ca - (cv - pv) * sa, pv + (cu - pu) * sa + (cv - pv) * ca)
            tool = base_tool.rotate(centre, centre + axis, a)
            cap_pt = world(plane, c[0], c[1], cap0[0].toTuple()[PLANE_AXES[plane][2]])
            copies.append((tool, c, (cap_pt, cap0[1])))
    for k, (tool, c, _) in enumerate(copies, 2):
        check_center(f"{fid} (copy {k})", c, plane, f"plane {plane}", bbox)
    for k, (tool, c, _) in enumerate(copies, 2):
        part = _combine(part, tool, "cut", fid, f"copy {k} of {src['id']} at {pt(c)}")
    return part, ([t for t, _, _ in copies], [cp for _, _, cp in copies])


def _edge_op(f, part, records):
    fid, kind = f["id"], f["type"]
    size = f["radius"] if kind == "fillet" else f["size"]
    what = "radius" if kind == "fillet" else "size"
    if size <= 0:
        raise SpecError([f"feature {fid}: {kind} {what} must be positive (got {fmt(size)})"])
    edges = select_edges(part, f["edges"], records, fid)
    if not edges:
        raise SpecError([f"feature {fid}: edge selector '{f['edges']}' matched no edges on the part"])
    wall = smallest_wall(part, edges)
    if wall and size >= wall[0] - 1e-6:
        raise SpecError([f"feature {fid}: {kind} {what} {fmt(size)} is larger than the wall it sits on "
                         f"(the face at {pt(wall[1].toTuple())} is only {fmt(round(wall[0], 3))} mm across); "
                         f"use less than {fmt(round(wall[0], 3))}"])
    try:
        new = part.fillet(size, edges) if kind == "fillet" else part.chamfer(size, None, edges)
        ok = new.isValid() and len(new.Solids()) == 1
    except Exception:  # noqa: BLE001 - OCC raises StdFail_NotDone and friends
        ok = False
    if not ok:
        hint = f" (the smallest adjacent flat face is {fmt(round(wall[0], 3))} mm across)" if wall else ""
        raise SpecError([f"feature {fid}: {kind} {what} {fmt(size)} on {len(edges)} edges ('{f['edges']}') failed in the "
                         f"CAD kernel; the {what} is probably too large for the geometry{hint}. Try a smaller {what}."])
    return new.Solids()[0], (None, None)


def _shell(f, part, bbox):
    fid, t = f["id"], f["thickness"]
    size = bbox[1] - bbox[0]
    if t <= 0 or t >= size.min() / 2:
        raise SpecError([f"feature {fid}: shell thickness must be positive and less than half the smallest part "
                         f"dimension ({fmt(round(size.min() / 2, 3))}); got {fmt(t)}"])
    faces = []
    for name in f["open_faces"]:
        plane, side = FACES[name]
        iw = PLANE_AXES[plane][2]
        target = bbox[1][iw] if side == "max" else bbox[0][iw]
        normal = [0.0, 0.0, 0.0]
        normal[iw] = 1.0 if side == "max" else -1.0
        hits = [fc for fc in part.Faces() if fc.geomType() == "PLANE"
                and fc.normalAt(fc.Center()).dot(V(*normal)) > 0.999 and abs(fc.Center().toTuple()[iw] - target) < 1e-4]
        if not hits:
            raise SpecError([f"feature {fid}: there is no flat face on the {name} of the part to open"])
        faces.extend(hits)
    try:
        new = part.hollow(faces, -t, kind="arc")
        ok = new.isValid() and len(new.Solids()) == 1 and new.Volume() < part.Volume() - 1e-6
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise SpecError([f"feature {fid}: shell with {fmt(t)} mm walls failed in the CAD kernel; try thinner walls, "
                         "or shell before adding fillets and small features"])
    return new.Solids()[0], (None, None)


def build(feats):
    """Run the feature list. Returns (solid, feature_status). Raises SpecError on the first failing feature."""
    part, status, records, by_id, boxes = None, [], {}, {}, {}
    for idx, f in enumerate(feats):
        fid, t = f["id"], f["type"]
        before = part.Volume() if part is not None else 0.0
        bbox = bbox_of(part) if part is not None else None
        try:
            if t == "extrude":
                part, rec = _extrude(f, part)
            elif t == "revolve":
                part, rec = _revolve(f, part)
            elif t in ("hole", "slot"):
                tool, plane, center, cap = (hole_tool if t == "hole" else slot_tool)(f, bbox, boxes)
                part = _combine(part, tool, "cut", fid, f"{t} at {pt(center)}")
                rec = ([tool], [cap])
            elif t in ("linear_pattern", "circular_pattern"):
                part, rec = _pattern(f, part, records, by_id, bbox)
            elif t in ("fillet", "chamfer"):
                part, rec = _edge_op(f, part, records)
            else:
                part, rec = _shell(f, part, bbox)
        except SpecError as e:
            status.append({"id": fid, "type": t, "status": "error", "message": " ".join(e.errors)})
            status += [{"id": g["id"], "type": g["type"], "status": "skipped"} for g in feats[idx + 1:]]
            raise SpecError(e.errors, status) from None
        if rec[0] is not None:
            records[fid] = rec
        if t in ("extrude", "revolve") and f["operation"] == "add":
            boxes[fid] = bbox_of(rec[0][0])
        by_id[fid] = f
        status.append({"id": fid, "type": t, "status": "ok",
                       "volume_delta_mm3": round(part.Volume() - before, 4)})
    if not part.isValid():
        raise SpecError(["the finished part is not a valid solid (the CAD kernel reports bad topology); "
                         "check overlapping or touching features"], status)
    return part, status


# ---------------------------------------------------------------- exports
def step_bytes(solid, name):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "part.step")
        cq.exporters.export(solid, path, cq.exporters.ExportTypes.STEP)
        text = open(path, encoding="latin-1").read()
    # fixed timestamp so the same spec gives byte-identical files
    text = re.sub(r"(FILE_NAME\('[^']*',)'[^']*'", r"\1'2026-01-01T00:00:00'", text, count=1)
    # OCC numbers product names with a process-wide counter ("... translator 7.9 2"); use the part name
    safe = re.sub(r"[^A-Za-z0-9_\- ]", "_", name)
    text = re.sub(r"Open CASCADE STEP translator [\d.]+ \d+", safe, text)
    return text.encode("latin-1")


def _r(x, nd=6):
    return [round(float(v), nd) for v in x]


def compile(spec, overrides=None, mm_per_px=0.2):  # noqa: A001 - the name is the point
    """Compile a PartSpec (model, dict or JSON path). Raises SpecError with readable messages."""
    timings, t0 = {}, time.perf_counter()
    try:
        spec = load(spec)
    except ValidationError as e:
        raise SpecError([_pydantic_msg(err) for err in e.errors()]) from None
    params, feats = resolve(spec, overrides)
    check_references(feats)
    timings["resolve"] = time.perf_counter() - t0

    t = time.perf_counter()
    solid, status = build(feats)
    timings["build"] = time.perf_counter() - t

    t = time.perf_counter()
    lo, hi = bbox_of(solid)
    verts, tris = solid.tessellate(MESH_TOL, MESH_ANG)
    verts = np.array([v.toTuple() for v in verts], dtype=float)
    tris = np.array(tris, dtype=np.int64)
    stl = render.stl_bytes(verts, tris, f"hephaestus {spec.name}")
    step = step_bytes(solid, spec.name)
    timings["export"] = time.perf_counter() - t

    t = time.perf_counter()
    sil = render.silhouettes(verts, tris, lo, hi, mm_per_px)
    timings["silhouettes"] = time.perf_counter() - t

    warnings = []
    if np.abs(lo).max() > 1e-3:
        warnings.append(f"the part's bbox starts at {pt(_r(lo, 3))}, not at the origin; the convention is "
                        "origin at the lower-left-front corner of the bounding box")
    metrics = {
        "name": spec.name,
        "volume_mm3": round(solid.Volume(), 4),
        "bbox": {"min": _r(lo), "max": _r(hi), "size": _r(hi - lo)},
        "faces": len(solid.Faces()),
        "edges": len(solid.Edges()),
        "triangles": int(len(tris)),
        "parameters": {k: round(v, 6) for k, v in params.items()},
        "features": status,
        "silhouettes": {view: s.meta() for view, s in sil.items()},
        "warnings": warnings,
    }
    timings["total"] = time.perf_counter() - t0
    return Result(spec, params, solid, step, stl, metrics, sil, warnings, timings)


def _pydantic_msg(err):
    loc = ".".join(str(x) for x in err["loc"])
    return f"spec {loc}: {err['msg']}" if loc else f"spec: {err['msg']}"


def write_outputs(result, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    files = {"part.step": result.step, "part.stl": result.stl}
    files.update({f"{v}.png": s.png() for v, s in result.silhouettes.items()})
    for name, data in files.items():
        with open(os.path.join(out_dir, name), "wb") as fh:
            fh.write(data)
    with open(os.path.join(out_dir, "metrics.json"), "w") as fh:
        json.dump(result.metrics, fh, indent=2)
    return sorted(files) + ["metrics.json"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m hephaestus.compile", description=__doc__.split("\n")[0])
    ap.add_argument("spec", help="part spec JSON file")
    ap.add_argument("--out", default="out", help="output directory")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE", help="override a parameter")
    ap.add_argument("--mm-per-px", type=float, default=0.2, help="silhouette scale")
    args = ap.parse_args(argv)
    overrides = {}
    for item in args.set:
        name, _, value = item.partition("=")
        overrides[name.strip()] = float(value) if re.fullmatch(r"\s*-?[\d.]+\s*", value) else value
    try:
        result = compile(args.spec, overrides, args.mm_per_px)
    except SpecError as e:
        print("Spec errors:", file=sys.stderr)
        for msg in e.errors:
            print(f"  - {msg}", file=sys.stderr)
        return 1
    files = write_outputs(result, args.out)
    m = result.metrics
    size = " x ".join(fmt(round(s, 3)) for s in m["bbox"]["size"])
    print(f"{m['name']}: {size} mm, volume {m['volume_mm3']:.1f} mm3, {m['faces']} faces, "
          f"{len(m['features'])} features ok, {result.timings['total']:.2f} s")
    for w in result.warnings:
        print(f"  warning: {w}")
    print(f"wrote {', '.join(files)} to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
